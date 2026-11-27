# -*- coding: utf-8 -*-
import json
import os
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request

try:
    from core.paths import get_data_dir, get_template_dir
    from core.study_assistant import ask_assistant, get_public_ai_config, save_ai_config
    from platforms.uooc.uooc_engine import UoocClient
    from platforms.uooc.uooc_runner import TaskManager
except ImportError:
    from pathlib import Path
    from study_assistant import ask_assistant, get_public_ai_config, save_ai_config
    from engine import UoocClient
    from runner import TaskManager

    def get_data_dir():
        return Path(os.path.dirname(os.path.abspath(__file__)))

    def get_template_dir(_name: str) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def _format_mmss(sec: float) -> str:
    s = max(0, int(sec))
    m, r = divmod(s, 60)
    return f"{m:02d}:{r:02d}"


def create_app(data_dir: Optional[str] = None, template_dir: Optional[str] = None) -> Flask:
    tpl_folder = template_dir or get_template_dir("uooc")
    app = Flask("uooc_console", template_folder=tpl_folder)
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    base_dir = data_dir or str(get_data_dir())
    accounts_file = os.path.join(base_dir, "uooc_accounts.json")
    legacy_config_file = os.path.join(base_dir, "uooc_config.json")

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

    class UoocAccountSession:
        def __init__(
            self,
            account_id: str,
            cookie: str,
            user_name: str = "",
            school_name: str = "",
            avatar: str = "",
            remark: str = "",
            speed: float = 1.5,
            interval: int = 15,
            logged_in: bool = True,
        ):
            self.account_id = str(account_id)
            self.cookie = cookie.strip()
            self.user_name = user_name or f"UID {self.account_id}"
            self.school_name = school_name or ""
            self.avatar = avatar or ""
            self.remark = remark or ""
            self.speed = max(1.0, min(2.0, float(speed or 1.5)))
            self.interval = max(10, min(30, int(interval or 15)))
            self.logged_in = bool(logged_in and self.cookie)
            self.cached_courses: List[Dict[str, Any]] = []
            self.runner = TaskManager(
                account_label=self.display_label,
                global_log_cb=lambda entry: global_logs.append(entry),
            )
            self.runner.state["speed"] = self.speed
            self.runner.state["interval"] = self.interval

        @property
        def display_label(self) -> str:
            return f"{self.remark}({self.user_name})" if self.remark else self.user_name

        def sync_label(self) -> None:
            self.runner.account_label = self.display_label

        def add_log(self, msg: str, level: str = "INFO") -> None:
            self.sync_label()
            self.runner.log(msg, level)

        def to_summary(self) -> Dict[str, Any]:
            st = self.runner.get_state()
            pos = float(st.get("current_pos") or 0.0)
            dur = float(st.get("current_duration") or 0.0)
            return {
                "account_id": self.account_id,
                "user_name": self.user_name,
                "school_name": self.school_name,
                "avatar": self.avatar,
                "remark": self.remark,
                "display_label": self.display_label,
                "logged_in": self.logged_in,
                "status": st.get("status", "idle"),
                "running": bool(st.get("running")),
                "paused": bool(st.get("paused")),
                "action": st.get("action", "等待指令"),
                "current_course_name": st.get("current_course_name", ""),
                "current_course_id": st.get("current_course_id"),
                "current_video_title": st.get("current_video_title", ""),
                "current_pos": pos,
                "current_duration": dur,
                "time_str": f"{_format_mmss(pos)} / {_format_mmss(dur)}" if dur > 0 else "--:-- / --:--",
                "progress_percent": round(float(st.get("progress_percent") or 0.0), 1),
                "speed": self.speed,
                "interval": self.interval,
                "stats": st.get("stats") or {},
                "course_count": len(self.cached_courses),
            }

        def to_detail(self) -> Dict[str, Any]:
            summary = self.to_summary()
            summary["logs"] = self.runner.get_state().get("logs", [])
            return summary

        def to_persist_dict(self) -> Dict[str, Any]:
            return {
                "account_id": self.account_id,
                "cookie": self.cookie,
                "user_name": self.user_name,
                "school_name": self.school_name,
                "avatar": self.avatar,
                "remark": self.remark,
                "speed": self.speed,
                "interval": self.interval,
                "updated_at": int(time.time()),
            }

        def start_courses(self, cids: List[Any], speed: Optional[float] = None, interval: Optional[int] = None) -> tuple:
            if not self.logged_in or not self.cookie:
                return False, f"账号 {self.display_label} 未登录或 Cookie 已失效"
            sp = max(1.0, min(2.0, float(speed if speed is not None else self.speed)))
            iv = max(10, min(30, int(interval if interval is not None else self.interval)))
            self.speed = sp
            self.interval = iv
            target_cids = list(cids or [])
            if not target_cids:
                client = UoocClient(self.cookie)
                ok, courses = client.get_courses()
                if not ok or not courses:
                    return False, f"未找到可用课程: {courses}"
                self.cached_courses = courses
                target_cids = [c["cid"] for c in courses if c.get("cid")]
            if not target_cids:
                return False, "未找到任何可用课程"
            self.sync_label()
            ok, msg = self.runner.start_courses(self.cookie, target_cids, sp, iv)
            return ok, (f"账号 {self.display_label} 已启动（{len(target_cids)} 门课）" if ok else msg)

    accounts: Dict[str, UoocAccountSession] = {}

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
                add_global_log(f"保存 UOOC 多账号配置失败: {e}", "ERROR")
                return False

    def register_or_update_account(
        cookie: str,
        remark: str = "",
        speed: Optional[float] = None,
        interval: Optional[int] = None,
    ) -> tuple:
        cookie = (cookie or "").strip()
        if not cookie:
            return None, None, "Cookie 不能为空"
        client = UoocClient(cookie)
        ok, user = client.get_user()
        if not ok or not isinstance(user, dict):
            return None, None, f"Cookie 校验失败: {user}"

        uid = str(user.get("id") or "").strip()
        if not uid:
            return None, None, "未能解析 UOOC 用户 ID"
        uname = (user.get("name") or user.get("nick") or f"UID {uid}").strip()
        school = (user.get("school_name") or "").strip()
        avatar = (user.get("smallavatar") or "").strip()

        with accounts_lock:
            existing = accounts.get(uid)
            if existing:
                existing.cookie = cookie
                existing.user_name = uname
                existing.school_name = school
                existing.avatar = avatar
                if remark:
                    existing.remark = remark.strip()[:24]
                if speed is not None:
                    existing.speed = max(1.0, min(2.0, float(speed)))
                if interval is not None:
                    existing.interval = max(10, min(30, int(interval)))
                existing.logged_in = True
                existing.sync_label()
                acc = existing
            else:
                acc = UoocAccountSession(
                    account_id=uid,
                    cookie=cookie,
                    user_name=uname,
                    school_name=school,
                    avatar=avatar,
                    remark=remark.strip()[:24] if remark else "",
                    speed=float(speed if speed is not None else 1.5),
                    interval=int(interval if interval is not None else 15),
                    logged_in=True,
                )
                accounts[uid] = acc

        save_accounts_store()
        acc.add_log("🍪 Cookie 校验通过，已接入多账号并发池", "SUCCESS")
        return acc, user, "Cookie 校验并加入多账号池成功"

    def _load_initial_accounts() -> None:
        if os.path.exists(accounts_file):
            try:
                with open(accounts_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    for aid, item in raw.items():
                        if not isinstance(item, dict) or not item.get("cookie"):
                            continue
                        acc = UoocAccountSession(
                            account_id=str(item.get("account_id") or aid),
                            cookie=str(item.get("cookie") or ""),
                            user_name=str(item.get("user_name") or ""),
                            school_name=str(item.get("school_name") or ""),
                            avatar=str(item.get("avatar") or ""),
                            remark=str(item.get("remark") or ""),
                            speed=float(item.get("speed") or 1.5),
                            interval=int(item.get("interval") or 15),
                            logged_in=True,
                        )
                        try:
                            ok, u = UoocClient(acc.cookie).get_user()
                            if ok and isinstance(u, dict):
                                acc.user_name = (u.get("name") or u.get("nick") or acc.user_name).strip()
                                acc.school_name = (u.get("school_name") or acc.school_name).strip()
                                acc.avatar = (u.get("smallavatar") or acc.avatar).strip()
                                acc.logged_in = True
                                acc.sync_label()
                                acc.add_log("已恢复本地持久化 UOOC 会话", "INFO")
                            else:
                                acc.logged_in = False
                                acc.runner.state["action"] = "Cookie 已过期，请重新导入"
                        except Exception:
                            acc.add_log("启动时网络未就绪，已保留本地会话凭证", "WARN")
                        accounts[acc.account_id] = acc
                return
            except Exception as e:
                add_global_log(f"加载 uooc_accounts.json 失败: {e}", "WARN")

        # Seamless migration from legacy single-account uooc_config.json
        if os.path.exists(legacy_config_file):
            try:
                with open(legacy_config_file, "r", encoding="utf-8") as f:
                    legacy = json.load(f)
                cookie = (legacy.get("cookie") or "").strip() if isinstance(legacy, dict) else ""
                if cookie:
                    acc, _, msg = register_or_update_account(
                        cookie=cookie,
                        remark="主账号",
                        speed=legacy.get("speed", 1.5),
                        interval=legacy.get("interval", 15),
                    )
                    if acc:
                        add_global_log(f"已自动从旧版配置迁移 UOOC 账号: {acc.display_label}", "SUCCESS")
                    else:
                        # Still preserve cookie so user doesn't lose it if offline
                        fallback = UoocAccountSession(
                            account_id="legacy",
                            cookie=cookie,
                            user_name="历史账号（待校验）",
                            remark="主账号",
                            speed=float(legacy.get("speed") or 1.5),
                            interval=int(legacy.get("interval") or 15),
                            logged_in=False,
                        )
                        fallback.runner.state["action"] = msg
                        accounts["legacy"] = fallback
                        save_accounts_store()
            except Exception as e:
                add_global_log(f"迁移旧版 uooc_config.json 失败: {e}", "WARN")

    _load_initial_accounts()

    def _get_target(account_id: Optional[str] = None) -> Optional[UoocAccountSession]:
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
                "code": 1,
                "ok": True,
                "accounts": acc_list,
                "total": len(acc_list),
                "running_count": sum(1 for a in acc_list if a["running"]),
            }
        )

    @app.route("/api/accounts/<account_id>/remark", methods=["POST"])
    def api_account_remark(account_id: str):
        acc = _get_target(account_id)
        if not acc:
            return jsonify({"code": 0, "ok": False, "msg": "账号不存在"}), 404
        acc.remark = ((request.json or {}).get("remark") or "").strip()[:24]
        acc.sync_label()
        save_accounts_store()
        return jsonify({"code": 1, "ok": True, "msg": "备注已更新", "account": acc.to_summary()})

    @app.route("/api/accounts/<account_id>", methods=["DELETE"])
    @app.route("/api/accounts/<account_id>/delete", methods=["POST"])
    def api_delete_account(account_id: str):
        with accounts_lock:
            acc = accounts.get(str(account_id))
            if not acc:
                return jsonify({"code": 0, "ok": False, "msg": "账号不存在"}), 404
            acc.runner.stop()
            label = acc.display_label
            del accounts[str(account_id)]
        save_accounts_store()
        add_global_log(f"🗑 已移除 UOOC 账号：{label}", "WARN")
        return jsonify({"code": 1, "ok": True, "msg": f"已移除账号 {label}"})

    @app.route("/api/config", methods=["GET"])
    def get_config():
        acc = _get_target(request.args.get("account_id"))
        if acc:
            return jsonify({"speed": acc.speed, "interval": acc.interval, "has_cookie": bool(acc.cookie)})
        return jsonify({"speed": 1.5, "interval": 15, "has_cookie": False})

    @app.route("/api/cookie", methods=["POST"])
    def set_cookie():
        data = request.json or {}
        acc, user, msg = register_or_update_account(
            cookie=data.get("cookie", ""),
            remark=data.get("remark", ""),
            speed=data.get("speed"),
            interval=data.get("interval"),
        )
        if not acc:
            return jsonify({"code": 0, "ok": False, "msg": msg})
        return jsonify(
            {
                "code": 1,
                "ok": True,
                "msg": msg,
                "account_id": acc.account_id,
                "user": user,
                "account": acc.to_summary(),
            }
        )

    @app.route("/api/user", methods=["GET"])
    def get_user():
        acc = _get_target(request.args.get("account_id"))
        if not acc or not acc.cookie:
            return jsonify({"code": 0, "msg": "尚未添加 UOOC 账号"})
        ok, res = UoocClient(acc.cookie).get_user()
        if not ok:
            acc.logged_in = False
            return jsonify({"code": 0, "msg": f"获取用户信息失败: {res}"})
        acc.logged_in = True
        return jsonify({"code": 1, "user": res, "account": acc.to_summary()})

    @app.route("/api/courses", methods=["GET"])
    def get_courses():
        acc = _get_target(request.args.get("account_id"))
        if not acc or not acc.cookie:
            return jsonify({"code": 0, "ok": False, "msg": "请先添加并选中一个 UOOC 账号", "courses": []})
        ok, courses = UoocClient(acc.cookie).get_courses()
        if not ok:
            return jsonify({"code": 0, "ok": False, "msg": f"获取课程失败: {courses}", "courses": []})
        acc.cached_courses = courses
        return jsonify(
            {
                "code": 1,
                "ok": True,
                "account_id": acc.account_id,
                "user_name": acc.user_name,
                "courses": courses,
                "count": len(courses),
            }
        )

    @app.route("/api/start", methods=["POST"])
    def start_task():
        data = request.json or {}
        acc = _get_target(data.get("account_id"))
        if not acc:
            return jsonify({"code": 0, "ok": False, "msg": "未找到目标账号，请先添加 Cookie"})
        ok, msg = acc.start_courses(
            cids=data.get("cids") or [],
            speed=data.get("speed"),
            interval=data.get("interval"),
        )
        save_accounts_store()
        return jsonify({"code": 1 if ok else 0, "ok": ok, "msg": msg, "account_id": acc.account_id})

    @app.route("/api/stop", methods=["POST"])
    def stop_task():
        data = request.json or {}
        acc = _get_target(data.get("account_id"))
        if not acc:
            return jsonify({"code": 0, "ok": False, "msg": "账号不存在"})
        ok = acc.runner.stop()
        return jsonify({"code": 1 if ok else 0, "ok": ok, "msg": "已发送停止请求" if ok else "当前无运行任务"})

    @app.route("/api/pause", methods=["POST"])
    def pause_task():
        data = request.json or {}
        acc = _get_target(data.get("account_id"))
        if not acc:
            return jsonify({"code": 0, "ok": False, "msg": "账号不存在"})
        ok, status, msg = acc.runner.pause()
        return jsonify({"code": 1 if ok else 0, "ok": ok, "status": status, "msg": msg})

    @app.route("/api/start_all", methods=["POST"])
    def start_all_accounts():
        data = request.json or {}
        speed = max(1.0, min(2.0, float(data.get("speed", 1.5))))
        interval = max(10, min(30, int(data.get("interval", 15))))
        started, skipped = [], []
        with accounts_lock:
            snap = list(accounts.values())
        for acc in snap:
            if not acc.logged_in:
                skipped.append(f"{acc.display_label}(凭证失效)")
                continue
            if acc.runner.state["running"]:
                skipped.append(f"{acc.display_label}(运行中)")
                continue
            ok, msg = acc.start_courses([], speed=speed, interval=interval)
            (started if ok else skipped).append(acc.display_label if ok else f"{acc.display_label}({msg})")
        save_accounts_store()
        return jsonify(
            {
                "code": 1,
                "ok": True,
                "started": started,
                "skipped": skipped,
                "msg": f"已同时启动 {len(started)} 个 UOOC 账号",
            }
        )

    @app.route("/api/pause_all", methods=["POST"])
    def pause_all_accounts():
        action = (request.json or {}).get("action", "toggle")
        affected = 0
        with accounts_lock:
            snap = list(accounts.values())
        target_pause = (action == "pause") or (action == "toggle" and any(a.runner.state["running"] and not a.runner.state["paused"] for a in snap))
        for acc in snap:
            if not acc.runner.state["running"]:
                continue
            if (target_pause and not acc.runner.state["paused"]) or (not target_pause and acc.runner.state["paused"]):
                acc.runner.pause()
                affected += 1
        return jsonify({"code": 1, "ok": True, "affected": affected, "msg": f"已{'暂停' if target_pause else '恢复'} {affected} 个账号"})

    @app.route("/api/stop_all", methods=["POST"])
    def stop_all_accounts():
        affected = 0
        with accounts_lock:
            for acc in accounts.values():
                if acc.runner.stop():
                    affected += 1
        return jsonify({"code": 1, "ok": True, "affected": affected, "msg": f"已停止 {affected} 个账号"})

    @app.route("/api/status", methods=["GET"])
    def get_status():
        req_aid = (request.args.get("account_id") or "").strip()
        with accounts_lock:
            acc_list = [acc.to_summary() for acc in accounts.values()]
            active = accounts.get(req_aid) if req_aid in accounts else (next(iter(accounts.values())) if accounts else None)
            active_detail = active.to_detail() if active else None

        resp: Dict[str, Any] = {
            "accounts": acc_list,
            "total_accounts": len(acc_list),
            "running_count": sum(1 for a in acc_list if a["running"]),
            "active_account": active_detail,
            "global_logs": list(global_logs)[-150:],
        }
        if active_detail:
            for k in (
                "status",
                "running",
                "paused",
                "action",
                "current_course_name",
                "current_course_id",
                "current_video_title",
                "current_pos",
                "current_duration",
                "progress_percent",
                "speed",
                "interval",
                "stats",
                "logs",
            ):
                resp[k] = active_detail[k]
        else:
            resp.update(
                {
                    "status": "idle",
                    "running": False,
                    "paused": False,
                    "action": "请先添加 UOOC 账号",
                    "current_course_name": "",
                    "current_video_title": "",
                    "current_pos": 0.0,
                    "current_duration": 0.0,
                    "progress_percent": 0.0,
                    "speed": 1.5,
                    "interval": 15,
                    "stats": {},
                    "logs": list(global_logs)[-120:],
                }
            )
        return jsonify(resp)

    @app.route("/api/assistant/config", methods=["GET", "POST"])
    def assistant_config():
        if request.method == "GET":
            return jsonify({"code": 1, "config": get_public_ai_config()})
        try:
            saved = save_ai_config(request.json or {})
            return jsonify({"code": 1, "msg": "AI 配置已保存（三平台共用）", "config": saved})
        except Exception as e:
            return jsonify({"code": 0, "msg": f"保存配置失败: {e}"}), 500

    @app.route("/api/assistant/ask", methods=["POST"])
    def assistant_ask():
        data = request.json or {}
        query = (data.get("query") or "").strip()
        if not query:
            return jsonify({"code": 0, "ok": False, "msg": "请输入题目或讨论内容"}), 400
        res = ask_assistant(
            query=query,
            mode=data.get("mode", "exercise"),
            course_name=(data.get("course_name") or "").strip(),
            use_search=bool(data.get("use_search", True)),
        )
        return jsonify({"code": 1 if res.get("ok") else 0, **res})

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("UOOC_PORT", "51088")), threaded=True)
