# -*- coding: utf-8 -*-
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional
from urllib.parse import unquote_plus

import requests
from flask import Flask, jsonify, render_template, request

try:
    from core.paths import get_bundle_dir, get_data_dir, get_static_dir, get_template_dir
    from core.study_assistant import ask_assistant, get_public_ai_config, save_ai_config
    ZHS_DIR = str(get_bundle_dir() / "platforms" / "zhs")
except ImportError:
    from pathlib import Path
    from study_assistant import ask_assistant, get_public_ai_config, save_ai_config
    ZHS_DIR = os.path.dirname(os.path.abspath(__file__))

    def get_data_dir():
        return Path(ZHS_DIR)

    def get_template_dir(_name: str) -> str:
        return os.path.join(ZHS_DIR, "templates")

    def get_static_dir(_name: str) -> str:
        return os.path.join(ZHS_DIR, "static")

if ZHS_DIR not in sys.path:
    sys.path.insert(0, ZHS_DIR)

import fucker  # noqa: E402
import utils  # noqa: E402
from fucker import Fucker  # noqa: E402
from utils import cookie_jar_to_list  # noqa: E402

# Thread-local binding so concurrent Fucker threads have isolated progress, pause/stop, and logs
_THREAD_CTX = threading.local()
_GLOBAL_LOG_LISTENERS: List[Any] = []
_HOOKS_LOCK = threading.Lock()


def _install_thread_safe_hooks() -> None:
    with _HOOKS_LOCK:
        if getattr(fucker, "_schale_multi_hooks", False):
            return

        orig_progress_bar = utils.progressBar
        orig_sleep = time.sleep

        def hooked_progress_bar(
            iteration,
            total,
            prefix="",
            suffix="",
            decimals=1,
            length=None,
            fill="#",
            progressbar_view=True,
        ):
            sess = getattr(_THREAD_CTX, "session", None)
            if sess is not None:
                try:
                    if total and total > 0:
                        sess.state["progress"] = min(round(100.0 * float(iteration) / float(total), 1), 100.0)
                        cur_m, cur_s = divmod(int(iteration), 60)
                        tot_m, tot_s = divmod(int(total), 60)
                        sess.state["time_str"] = f"{cur_m:02d}:{cur_s:02d} / {tot_m:02d}:{tot_s:02d}"
                    if prefix:
                        low = prefix.lower()
                        if "pause" in low:
                            sess.state["action"] = "微休避检中 (模拟真人暂停)"
                        elif "quiz" in low or "answering" in low:
                            sess.state["action"] = "自动处理随堂弹题中..."
                        else:
                            sess.state["action"] = "视频播放学习中"
                except Exception:
                    pass
            return orig_progress_bar(iteration, total, prefix, suffix, decimals, length, fill, False)

        def hooked_sleep(seconds):
            sess = getattr(_THREAD_CTX, "session", None)
            if sess is None:
                return orig_sleep(seconds)
            deadline = time.time() + float(seconds)
            while time.time() < deadline:
                if sess.stop_event.is_set():
                    raise InterruptedError("用户手动停止任务")
                while sess.pause_event.is_set():
                    if sess.stop_event.is_set():
                        raise InterruptedError("用户手动停止任务")
                    orig_sleep(0.2)
                orig_sleep(min(0.2, max(0.01, deadline - time.time())))

        class ThreadAwareLogHandler(logging.Handler):
            def emit(self, record):
                try:
                    msg = self.format(record)
                    sess = getattr(_THREAD_CTX, "session", None)
                    if sess is not None:
                        sess.add_log(msg, record.levelname)
                    else:
                        for cb in list(_GLOBAL_LOG_LISTENERS):
                            try:
                                cb(msg, record.levelname, "SYSTEM")
                            except Exception:
                                pass
                except Exception:
                    pass

        handler = ThreadAwareLogHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        fucker.logger.addHandler(handler)
        fucker.logger.setLevel("INFO")

        utils.progressBar = hooked_progress_bar
        fucker.progressBar = hooked_progress_bar
        fucker.time.sleep = hooked_sleep
        fucker._schale_multi_hooks = True


def _extract_zhs_identity(cookies_obj) -> tuple:
    """Return (account_id, user_name, school_name) from CASLOGC cookie when available."""
    cas_val = ""
    if isinstance(cookies_obj, list):
        for item in cookies_obj:
            if isinstance(item, dict) and item.get("name") == "CASLOGC":
                cas_val = str(item.get("value") or "")
                break
    elif isinstance(cookies_obj, dict):
        cas_val = str(cookies_obj.get("CASLOGC") or "")
    else:
        try:
            cas_val = str(cookies_obj.get("CASLOGC") or "")
        except Exception:
            cas_val = ""

    if cas_val:
        try:
            decoded = json.loads(unquote_plus(cas_val))
            if isinstance(decoded, dict):
                uid = str(decoded.get("uuid") or decoded.get("userId") or "").strip()
                uname = str(
                    decoded.get("realName")
                    or decoded.get("username")
                    or decoded.get("nickName")
                    or ""
                ).strip()
                school = str(decoded.get("schoolName") or "").strip()
                if uid:
                    return uid, (uname or f"智慧树-{uid[:8]}"), school
        except Exception:
            pass

    digest = hashlib.md5(json.dumps(cookies_obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]
    return f"zhs_{digest}", f"智慧树用户-{digest[:6]}", ""


def _serialize_cookies(cookies_obj) -> List[Dict[str, Any]]:
    if isinstance(cookies_obj, list):
        return cookies_obj
    if isinstance(cookies_obj, dict):
        return [{"name": k, "value": v, "domain": ".zhihuishu.com", "path": "/"} for k, v in cookies_obj.items()]
    return cookie_jar_to_list(cookies_obj)


def create_app(data_dir: Optional[str] = None, template_dir: Optional[str] = None, static_dir: Optional[str] = None) -> Flask:
    _install_thread_safe_hooks()

    tpl_folder = template_dir or get_template_dir("zhs")
    st_folder = static_dir or get_static_dir("zhs")
    app = Flask("zhs_console", template_folder=tpl_folder, static_folder=st_folder)
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    base_dir = data_dir or str(get_data_dir())
    accounts_file = os.path.join(base_dir, "zhs_accounts.json")
    legacy_cookies_files = [
        os.path.join(base_dir, "zhs_cookies.json"),
        os.path.join(base_dir, "cookies.json"),
    ]

    global_logs = deque(maxlen=350)
    accounts_lock = threading.RLock()

    def add_global_log(msg: str, level: str = "INFO", account_label: str = "SYSTEM") -> None:
        global_logs.append(
            {
                "time": time.strftime("%H:%M:%S"),
                "level": level,
                "account": account_label,
                "msg": msg,
            }
        )

    _GLOBAL_LOG_LISTENERS.append(add_global_log)

    class ZhsAccountSession:
        def __init__(
            self,
            account_id: str,
            cookies_list: List[Dict[str, Any]],
            user_name: str = "",
            school_name: str = "",
            remark: str = "",
            speed: float = 1.5,
        ):
            self.account_id = str(account_id)
            self.cookies_list = list(cookies_list or [])
            self.pause_event = threading.Event()
            self.stop_event = threading.Event()
            self.runner_lock = threading.Lock()
            self.worker_thread: Optional[threading.Thread] = None
            self.cached_courses: Dict[str, Dict[str, Any]] = {}
            self.log_buffer = deque(maxlen=220)

            self.fucker = Fucker(speed=speed, end_thre=1.0, tree_view=True, progressbar_view=True)
            self._bind_fucker_wrappers()

            self.state: Dict[str, Any] = {
                "account_id": self.account_id,
                "status": "idle",
                "logged_in": False,
                "user_name": user_name or f"智慧树-{self.account_id[:8]}",
                "school_name": school_name or "",
                "remark": remark or "",
                "current_course": None,
                "current_video": None,
                "progress": 0.0,
                "time_str": "--:-- / --:--",
                "speed": min(1.5, max(1.0, float(speed or 1.5))),
                "action": "等待指令",
                "completed_videos": 0,
                "error_msg": "",
            }

            if self.cookies_list:
                self.apply_cookies(self.cookies_list)

        def _bind_fucker_wrappers(self) -> None:
            orig_zhidao = self.fucker.fuckZhidaoVideo
            orig_hike = self.fucker.fuckHikeVideo

            def wrapped_zhidao(RAC_id, video_id):
                try:
                    ctx = self.fucker.getZhidaoContext(RAC_id)
                    video = ctx.videos.get(video_id)
                    vname = video.name if video else f"小节 #{video_id}"
                    self.state["current_video"] = {"id": str(video_id), "name": vname}
                    self.state["action"] = f"正在学习: {vname}"
                    self.add_log(f"▶ 开始小节: {vname}", "INFO")
                except Exception:
                    self.state["current_video"] = {"id": str(video_id), "name": f"小节 #{video_id}"}
                res = orig_zhidao(RAC_id, video_id)
                self.state["completed_videos"] += 1
                return res

            def wrapped_hike(course_id, file_id, prev_time=0):
                try:
                    file_info = self.fucker.stuViewFile(course_id, file_id)
                    vname = file_info.name if file_info and file_info.name else f"课件 #{file_id}"
                    self.state["current_video"] = {"id": str(file_id), "name": vname}
                    self.state["action"] = f"正在学习: {vname}"
                    self.add_log(f"▶ 开始校内课件: {vname}", "INFO")
                except Exception:
                    self.state["current_video"] = {"id": str(file_id), "name": f"课件 #{file_id}"}
                res = orig_hike(course_id, file_id, prev_time)
                self.state["completed_videos"] += 1
                return res

            self.fucker.fuckZhidaoVideo = wrapped_zhidao
            self.fucker.fuckHikeVideo = wrapped_hike

        def apply_cookies(self, cookies_list: List[Dict[str, Any]]) -> None:
            self.cookies_list = list(cookies_list)
            self.fucker.cookies = self.cookies_list
            self.state["logged_in"] = True

        @property
        def display_label(self) -> str:
            remark = self.state.get("remark")
            uname = self.state.get("user_name") or self.account_id[:8]
            return f"{remark}({uname})" if remark else uname

        def add_log(self, msg: str, level: str = "INFO") -> None:
            entry = {
                "time": time.strftime("%H:%M:%S"),
                "level": level,
                "account": self.display_label,
                "msg": msg,
            }
            self.log_buffer.append(entry)
            global_logs.append(entry)

        def to_summary(self) -> Dict[str, Any]:
            return {
                **self.state,
                "display_label": self.display_label,
                "course_count": len(self.cached_courses),
            }

        def to_detail(self) -> Dict[str, Any]:
            data = self.to_summary()
            data["logs"] = list(self.log_buffer)[-120:]
            return data

        def to_persist_dict(self) -> Dict[str, Any]:
            return {
                "account_id": self.account_id,
                "user_name": self.state["user_name"],
                "school_name": self.state["school_name"],
                "remark": self.state["remark"],
                "speed": self.state["speed"],
                "cookies": self.cookies_list,
                "updated_at": int(time.time()),
            }

        def fetch_courses(self) -> tuple:
            course_list: List[Dict[str, Any]] = []
            errors: List[str] = []
            try:
                self.fucker.courses.zhidao = None
                for c in (self.fucker.getZhidaoList() or []):
                    course_list.append(
                        {
                            "id": c.secret,
                            "name": c.courseName,
                            "type": "zhidao",
                            "type_label": "共享学分课",
                            "progress": float(getattr(c, "progress", 0) or 0),
                            "school": getattr(c, "schoolName", ""),
                            "instructor": getattr(c, "instructorName", ""),
                            "cover": getattr(c, "courseImg", ""),
                        }
                    )
            except Exception as e:
                errors.append(f"获取共享学分课异常: {e}")

            try:
                self.fucker.courses.hike = None
                for c in (self.fucker.getHikeList() or []):
                    course_list.append(
                        {
                            "id": str(c.courseId),
                            "name": c.courseName,
                            "type": "hike",
                            "type_label": "校内学分课",
                            "progress": float(getattr(c, "progress", 0) or 0),
                            "school": getattr(c, "schoolName", ""),
                            "instructor": getattr(c, "teacherName", ""),
                            "cover": getattr(c, "courseCover", ""),
                        }
                    )
            except Exception as e:
                errors.append(f"获取校内学分课异常: {e}")

            if course_list:
                self.cached_courses = {str(c["id"]): c for c in course_list}
                if not self.state["school_name"] and course_list[0].get("school"):
                    self.state["school_name"] = course_list[0]["school"]
            return course_list, errors

        def start_courses(self, course_ids: List[str], speed: float) -> tuple:
            with self.runner_lock:
                if self.state["status"] in ("running", "paused") and self.worker_thread and self.worker_thread.is_alive():
                    return False, f"账号 {self.display_label} 已有任务正在运行中"
                if not self.state["logged_in"]:
                    return False, f"账号 {self.display_label} 未登录或凭证已失效"

                sp = min(1.5, max(1.0, float(speed or self.state.get("speed", 1.5))))
                target_ids = [str(cid) for cid in (course_ids or []) if str(cid).strip()]
                if not target_ids:
                    courses, errs = self.fetch_courses()
                    if not courses:
                        return False, "; ".join(errs) if errs else "未发现可学习的课程"
                    target_ids = [str(c["id"]) for c in courses if float(c.get("progress", 0)) < 99.9]
                    if not target_ids:
                        target_ids = [str(c["id"]) for c in courses]

                self.stop_event.clear()
                self.pause_event.clear()
                self.state["speed"] = sp
                self.worker_thread = threading.Thread(
                    target=self._run_worker,
                    args=(target_ids, sp),
                    name=f"zhs-{self.account_id[:8]}",
                    daemon=True,
                )
                self.worker_thread.start()
                return True, f"账号 {self.display_label} 已启动，共 {len(target_ids)} 门课程"

        def toggle_pause(self) -> tuple:
            if self.state["status"] == "running":
                self.pause_event.set()
                self.state["status"] = "paused"
                self.state["action"] = "任务已暂停"
                self.add_log("⏸ 任务已暂停", "WARNING")
                return True, "paused", "已暂停"
            if self.state["status"] == "paused":
                self.pause_event.clear()
                self.state["status"] = "running"
                self.state["action"] = "任务继续运行"
                self.add_log("▶ 任务已恢复运行", "INFO")
                return True, "running", "已恢复运行"
            return False, self.state["status"], "当前没有正在运行的任务"

        def stop(self) -> bool:
            if self.state["status"] in ("running", "paused"):
                self.stop_event.set()
                self.pause_event.clear()
                self.state["status"] = "stopped"
                self.state["action"] = "正在停止任务..."
                self.add_log("⏹ 已发送停止指令", "WARNING")
                return True
            return False

        def _run_worker(self, course_ids: List[str], speed: float) -> None:
            _THREAD_CTX.session = self
            self.state["status"] = "running"
            self.state["speed"] = speed
            self.state["completed_videos"] = 0
            self.state["error_msg"] = ""
            self.state["progress"] = 0.0
            self.state["time_str"] = "--:-- / --:--"
            self.fucker.speed = speed
            self.add_log(f"🚀 独立线程启动：共 {len(course_ids)} 门课程，倍速 {speed}x", "INFO")

            try:
                for idx, cid in enumerate(course_ids, 1):
                    if self.stop_event.is_set():
                        break
                    while self.pause_event.is_set() and not self.stop_event.is_set():
                        time.sleep(0.3)
                    if self.stop_event.is_set():
                        break

                    c_info = self.cached_courses.get(str(cid)) or {}
                    c_type = c_info.get("type") or ("zhidao" if re.match(r".*[a-zA-Z].*", str(cid)) else "hike")
                    c_name = c_info.get("name") or f"课程 #{cid}"
                    self.state["current_course"] = {"id": str(cid), "name": c_name, "type": c_type}
                    self.state["action"] = f"载入课程 ({idx}/{len(course_ids)}): {c_name}"
                    self.add_log(f"📚 [{idx}/{len(course_ids)}] 开始课程：{c_name} ({c_type})", "INFO")

                    try:
                        self.fucker.fuckCourse(str(cid))
                        self.add_log(f"✅ 课程已学完：{c_name}", "INFO")
                    except InterruptedError:
                        self.add_log("⏹ 任务已被用户停止", "WARNING")
                        break
                    except Exception as e:
                        self.add_log(f"❌ 课程执行出错 ({c_name}): {e}", "ERROR")

                if self.stop_event.is_set():
                    self.state["status"] = "stopped"
                    self.state["action"] = "任务已安全停止"
                else:
                    self.state["status"] = "idle"
                    self.state["action"] = "所有选中课程均已完成！"
                    self.state["progress"] = 100.0
                    self.add_log("🎉 全部选定课程学习完成！", "INFO")
            except InterruptedError:
                self.state["status"] = "stopped"
                self.state["action"] = "任务已手动停止"
            except Exception as e:
                self.state["status"] = "error"
                self.state["error_msg"] = str(e)
                self.state["action"] = f"异常终止: {e}"
                self.add_log(f"❌ 后台线程异常: {e}", "ERROR")
            finally:
                _THREAD_CTX.session = None

    accounts: Dict[str, ZhsAccountSession] = {}

    def save_accounts_store() -> bool:
        with accounts_lock:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(accounts_file)), exist_ok=True)
                payload = {aid: acc.to_persist_dict() for aid, acc in accounts.items()}
                tmp = accounts_file + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp, accounts_file)
                return True
            except Exception as e:
                add_global_log(f"保存智慧树多账号配置失败: {e}", "ERROR")
                return False

    def register_or_update_account(cookies_obj, remark: str = "", speed: float = 1.5) -> ZhsAccountSession:
        cookies_list = _serialize_cookies(cookies_obj)
        aid, uname, school = _extract_zhs_identity(cookies_list)
        # Validate via Fucker cookie parser (requires CASLOGC)
        test_fucker = Fucker(speed=speed)
        test_fucker.cookies = cookies_list
        if test_fucker.uuid:
            aid = str(test_fucker.uuid)

        with accounts_lock:
            existing = accounts.get(aid)
            if existing:
                existing.apply_cookies(cookies_list)
                if uname and not uname.startswith("智慧树用户-"):
                    existing.state["user_name"] = uname
                if school:
                    existing.state["school_name"] = school
                if remark:
                    existing.state["remark"] = remark.strip()[:24]
                existing.cached_courses.clear()
                acc = existing
            else:
                acc = ZhsAccountSession(
                    account_id=aid,
                    cookies_list=cookies_list,
                    user_name=uname,
                    school_name=school,
                    remark=remark.strip()[:24] if remark else "",
                    speed=speed,
                )
                accounts[aid] = acc

        save_accounts_store()
        return acc

    def _load_initial_accounts() -> None:
        if os.path.exists(accounts_file):
            try:
                with open(accounts_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    for aid, item in raw.items():
                        if not isinstance(item, dict) or not item.get("cookies"):
                            continue
                        try:
                            acc = ZhsAccountSession(
                                account_id=str(item.get("account_id") or aid),
                                cookies_list=item.get("cookies") or [],
                                user_name=str(item.get("user_name") or ""),
                                school_name=str(item.get("school_name") or ""),
                                remark=str(item.get("remark") or ""),
                                speed=float(item.get("speed") or 1.5),
                            )
                            acc.add_log("已恢复本地持久化智慧树会话", "INFO")
                            accounts[acc.account_id] = acc
                        except Exception as e:
                            add_global_log(f"账号 {aid} 凭证加载异常: {e}", "WARNING")
                return
            except Exception as e:
                add_global_log(f"加载 zhs_accounts.json 失败: {e}", "WARNING")

        for legacy_path in legacy_cookies_files:
            if os.path.exists(legacy_path):
                try:
                    with open(legacy_path, "r", encoding="utf-8") as f:
                        c_data = json.load(f)
                    if c_data:
                        acc = register_or_update_account(c_data, remark="主账号")
                        add_global_log(f"已从旧版凭证自动迁移智慧树账号: {acc.display_label}", "INFO")
                        break
                except Exception as e:
                    add_global_log(f"迁移旧版智慧树凭证失败: {e}", "WARNING")

    _load_initial_accounts()

    def _get_target(account_id: Optional[str] = None) -> Optional[ZhsAccountSession]:
        with accounts_lock:
            aid = (account_id or "").strip()
            if aid and aid in accounts:
                return accounts[aid]
            if not aid and accounts:
                return next(iter(accounts.values()))
            return None

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/accounts")
    def api_accounts():
        with accounts_lock:
            acc_list = [acc.to_summary() for acc in accounts.values()]
        return jsonify(
            {
                "code": 200,
                "ok": True,
                "accounts": acc_list,
                "total": len(acc_list),
                "running_count": sum(1 for a in acc_list if a["status"] == "running"),
            }
        )

    @app.route("/api/accounts/<account_id>/remark", methods=["POST"])
    def api_account_remark(account_id: str):
        acc = _get_target(account_id)
        if not acc:
            return jsonify({"code": 404, "ok": False, "msg": "账号不存在"}), 404
        acc.state["remark"] = ((request.json or {}).get("remark") or "").strip()[:24]
        save_accounts_store()
        return jsonify({"code": 200, "ok": True, "msg": "备注已更新", "account": acc.to_summary()})

    @app.route("/api/accounts/<account_id>", methods=["DELETE"])
    @app.route("/api/accounts/<account_id>/delete", methods=["POST"])
    def api_delete_account(account_id: str):
        with accounts_lock:
            acc = accounts.get(str(account_id))
            if not acc:
                return jsonify({"code": 404, "ok": False, "msg": "账号不存在"}), 404
            acc.stop()
            label = acc.display_label
            del accounts[str(account_id)]
        save_accounts_store()
        add_global_log(f"🗑 已移除智慧树账号：{label}", "WARNING")
        return jsonify({"code": 200, "ok": True, "msg": f"已移除账号 {label}"})

    @app.route("/api/status")
    def get_status():
        req_aid = (request.args.get("account_id") or "").strip()
        with accounts_lock:
            acc_list = [acc.to_summary() for acc in accounts.values()]
            active = accounts.get(req_aid) if req_aid in accounts else (next(iter(accounts.values())) if accounts else None)
            active_detail = active.to_detail() if active else None

        resp: Dict[str, Any] = {
            "accounts": acc_list,
            "total_accounts": len(acc_list),
            "running_count": sum(1 for a in acc_list if a["status"] == "running"),
            "active_account": active_detail,
            "global_logs": list(global_logs)[-150:],
        }
        if active_detail:
            for k in (
                "status",
                "logged_in",
                "user_name",
                "current_course",
                "current_video",
                "progress",
                "time_str",
                "speed",
                "action",
                "completed_videos",
                "error_msg",
                "logs",
            ):
                resp[k] = active_detail[k]
        else:
            resp.update(
                {
                    "status": "idle",
                    "logged_in": False,
                    "user_name": "",
                    "current_course": None,
                    "current_video": None,
                    "progress": 0.0,
                    "time_str": "--:-- / --:--",
                    "speed": 1.5,
                    "action": "请先添加智慧树账号",
                    "completed_videos": 0,
                    "error_msg": "",
                    "logs": list(global_logs)[-120:],
                }
            )
        return jsonify(resp)

    @app.route("/api/qr")
    def get_qr():
        try:
            r = requests.get("https://passport.zhihuishu.com/qrCodeLogin/getLoginQrImg", timeout=10).json()
            if r.get("status") == 200 or "qrToken" in r:
                return jsonify({"code": 200, "qrToken": r["qrToken"], "img": f"data:image/png;base64,{r['img']}"})
            return jsonify({"code": 500, "msg": "获取二维码失败"}), 500
        except Exception as e:
            return jsonify({"code": 500, "msg": str(e)}), 500

    @app.route("/api/qr/poll")
    def poll_qr():
        token = (request.args.get("token") or "").strip()
        remark = (request.args.get("remark") or "").strip()
        if not token:
            return jsonify({"code": 400, "msg": "缺少 token 参数"}), 400
        query_url = "https://passport.zhihuishu.com/qrCodeLogin/getLoginQrInfo"
        login_page = "https://passport.zhihuishu.com/login?service=https://onlineservice-api.zhihuishu.com/login/gologin"
        try:
            r = requests.get(query_url, params={"qrToken": token}, timeout=10).json()
            status = r.get("status")
            if status == -1:
                return jsonify({"status": "waiting", "msg": "等待手机扫码..."})
            if status == 0:
                return jsonify({"status": "scanned", "msg": "已扫码，请在智慧树 APP 上确认登录"})
            if status == 1:
                sess = requests.Session()
                sess.headers.update(
                    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"}
                )
                sess.get(login_page, params={"pwd": r.get("oncePassword")}, timeout=10)
                acc = register_or_update_account(sess.cookies, remark=remark)
                acc.add_log("📱 扫码登录成功！已加入多账号并发池", "INFO")
                return jsonify(
                    {
                        "status": "success",
                        "msg": f"扫码登录成功 ({acc.display_label})",
                        "account_id": acc.account_id,
                        "account": acc.to_summary(),
                    }
                )
            if status == 2:
                return jsonify({"status": "expired", "msg": "二维码已失效，请刷新"})
            if status == 3:
                return jsonify({"status": "canceled", "msg": "手机端已取消登录"})
            return jsonify({"status": "error", "msg": f"未知状态: {r.get('msg', '')}"})
        except Exception as e:
            return jsonify({"status": "error", "msg": str(e)}), 500

    @app.route("/api/cookie", methods=["POST"])
    def set_cookie():
        data = request.json or {}
        cookie_str = (data.get("cookie") or "").strip()
        remark = (data.get("remark") or "").strip()
        if not cookie_str:
            return jsonify({"code": 400, "ok": False, "msg": "Cookie 不能为空"}), 400
        try:
            if cookie_str.startswith("[") or cookie_str.startswith("{"):
                parsed = json.loads(cookie_str)
                acc = register_or_update_account(parsed, remark=remark)
            else:
                jar = requests.cookies.RequestsCookieJar()
                for part in cookie_str.split(";"):
                    if "=" in part:
                        k, v = part.strip().split("=", 1)
                        jar.set(k, v, domain=".zhihuishu.com")
                acc = register_or_update_account(jar, remark=remark)
            acc.add_log("🍪 Cookie 导入成功，已加入多账号并发池", "INFO")
            return jsonify(
                {
                    "code": 200,
                    "ok": True,
                    "msg": f"Cookie 导入成功 ({acc.display_label})",
                    "account_id": acc.account_id,
                    "account": acc.to_summary(),
                }
            )
        except Exception as e:
            return jsonify({"code": 500, "ok": False, "msg": f"Cookie 解析失败（需包含有效 CASLOGC）: {e}"}), 500

    @app.route("/api/courses")
    def get_courses():
        acc = _get_target(request.args.get("account_id"))
        if not acc or not acc.state["logged_in"]:
            return jsonify({"code": 401, "ok": False, "msg": "请先添加并登录智慧树账号", "courses": []}), 401
        course_list, errors = acc.fetch_courses()
        if not course_list and errors:
            return jsonify({"code": 500, "ok": False, "msg": "; ".join(errors), "courses": []}), 500
        return jsonify(
            {
                "code": 200,
                "ok": True,
                "account_id": acc.account_id,
                "courses": course_list,
                "count": len(course_list),
            }
        )

    @app.route("/api/start", methods=["POST"])
    def start_task():
        data = request.json or {}
        acc = _get_target(data.get("account_id"))
        if not acc:
            return jsonify({"code": 404, "ok": False, "msg": "未找到目标账号"}), 404
        ok, msg = acc.start_courses(data.get("course_ids") or [], float(data.get("speed", acc.state.get("speed", 1.5))))
        save_accounts_store()
        return jsonify({"code": 200 if ok else 400, "ok": ok, "msg": msg, "account_id": acc.account_id}), (200 if ok else 400)

    @app.route("/api/pause", methods=["POST"])
    def toggle_pause():
        acc = _get_target((request.json or {}).get("account_id"))
        if not acc:
            return jsonify({"code": 404, "ok": False, "msg": "账号不存在"}), 404
        ok, status, msg = acc.toggle_pause()
        return jsonify({"code": 200 if ok else 400, "ok": ok, "status": status, "msg": msg}), (200 if ok else 400)

    @app.route("/api/stop", methods=["POST"])
    def stop_task():
        acc = _get_target((request.json or {}).get("account_id"))
        if not acc:
            return jsonify({"code": 404, "ok": False, "msg": "账号不存在"}), 404
        ok = acc.stop()
        return jsonify({"code": 200 if ok else 400, "ok": ok, "msg": "停止指令已发送" if ok else "当前无运行任务"}), (200 if ok else 400)

    @app.route("/api/start_all", methods=["POST"])
    def start_all_accounts():
        speed = min(1.5, max(1.0, float((request.json or {}).get("speed", 1.5))))
        started, skipped = [], []
        with accounts_lock:
            snap = list(accounts.values())
        for acc in snap:
            if not acc.state["logged_in"]:
                skipped.append(f"{acc.display_label}(未登录)")
                continue
            if acc.state["status"] in ("running", "paused") and acc.worker_thread and acc.worker_thread.is_alive():
                skipped.append(f"{acc.display_label}(运行中)")
                continue
            ok, msg = acc.start_courses([], speed)
            (started if ok else skipped).append(acc.display_label if ok else f"{acc.display_label}({msg})")
        save_accounts_store()
        return jsonify(
            {
                "code": 200,
                "ok": True,
                "started": started,
                "skipped": skipped,
                "msg": f"已同时启动 {len(started)} 个智慧树账号",
            }
        )

    @app.route("/api/pause_all", methods=["POST"])
    def pause_all_accounts():
        action = (request.json or {}).get("action", "toggle")
        affected = 0
        with accounts_lock:
            snap = list(accounts.values())
        target_pause = (action == "pause") or (action == "toggle" and any(a.state["status"] == "running" for a in snap))
        for acc in snap:
            if (target_pause and acc.state["status"] == "running") or (not target_pause and acc.state["status"] == "paused"):
                acc.toggle_pause()
                affected += 1
        return jsonify({"code": 200, "ok": True, "affected": affected, "msg": f"已{'暂停' if target_pause else '恢复'} {affected} 个账号"})

    @app.route("/api/stop_all", methods=["POST"])
    def stop_all_accounts():
        affected = 0
        with accounts_lock:
            for acc in accounts.values():
                if acc.stop():
                    affected += 1
        return jsonify({"code": 200, "ok": True, "affected": affected, "msg": f"已停止 {affected} 个账号"})

    @app.route("/api/assistant/config", methods=["GET", "POST"])
    def assistant_config():
        if request.method == "GET":
            return jsonify({"code": 200, "ok": True, "config": get_public_ai_config()})
        try:
            saved = save_ai_config(request.json or {})
            return jsonify({"code": 200, "ok": True, "msg": "AI 配置已保存（三平台共用）", "config": saved})
        except Exception as e:
            return jsonify({"code": 500, "ok": False, "msg": f"保存配置失败: {e}"}), 500

    @app.route("/api/assistant/ask", methods=["POST"])
    def assistant_ask():
        data = request.json or {}
        query = (data.get("query") or "").strip()
        if not query:
            return jsonify({"code": 400, "ok": False, "msg": "请输入题目或讨论内容"}), 400
        res = ask_assistant(
            query=query,
            mode=data.get("mode", "exercise"),
            course_name=(data.get("course_name") or "").strip(),
            use_search=bool(data.get("use_search", True)),
        )
        return jsonify({"code": 200 if res.get("ok") else 400, **res})

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("ZHS_PORT", "51089")), threaded=True)
