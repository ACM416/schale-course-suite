# -*- coding: utf-8 -*-
import json
import os
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request

from core.paths import get_data_dir, get_static_dir, get_template_dir
from core.study_assistant import ask_assistant, get_public_ai_config, save_ai_config
from platforms.cx.cx_engine import ChaoxingClient


def _format_mmss(sec: float) -> str:
    s = max(0, int(sec))
    m, r = divmod(s, 60)
    return f"{m:02d}:{r:02d}"


def create_app() -> Flask:
    app = Flask("cx_console", template_folder=get_template_dir("cx"), static_folder=get_static_dir("cx"))
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    accounts_file = str(get_data_dir() / "cx_accounts.json")
    global_logs = deque(maxlen=350)
    accounts_lock = threading.RLock()
    qr_auth_client = ChaoxingClient()

    def add_global_log(msg: str, level: str = "INFO", account_label: str = "SYSTEM") -> None:
        global_logs.append(
            {
                "time": time.strftime("%H:%M:%S"),
                "level": level,
                "account": account_label,
                "msg": msg,
            }
        )

    class AccountSession:
        def __init__(
            self,
            account_id: str,
            user_name: str = "",
            remark: str = "",
            speed: float = 1.5,
            cookies: Optional[List[Dict[str, Any]]] = None,
        ):
            self.account_id = str(account_id)
            self.client = ChaoxingClient()
            if cookies:
                self.client.load_cookies(cookies)

            self.pause_event = threading.Event()
            self.stop_event = threading.Event()
            self.runner_lock = threading.Lock()
            self.worker_thread: Optional[threading.Thread] = None
            self.cached_courses: Dict[str, Dict[str, Any]] = {}
            self.log_buffer = deque(maxlen=200)

            self.state: Dict[str, Any] = {
                "account_id": self.account_id,
                "status": "idle",
                "logged_in": bool(cookies),
                "user_name": user_name or f"UID {self.account_id}",
                "user_uid": self.account_id,
                "remark": remark or "",
                "current_course": None,
                "current_chapter": "",
                "current_task": None,
                "progress": 0.0,
                "time_str": "--:-- / --:--",
                "speed": max(1.0, min(float(speed or 1.5), 2.0)),
                "action": "等待指令",
                "completed_tasks": 0,
                "skipped_quizzes": 0,
                "error_msg": "",
            }

        @property
        def display_label(self) -> str:
            remark = self.state.get("remark")
            uname = self.state.get("user_name") or self.account_id
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
                "remark": self.state["remark"],
                "speed": self.state["speed"],
                "cookies": self.client.export_cookies(),
                "updated_at": int(time.time()),
            }

        def start_courses(self, selected_ids: List[str], speed: float) -> tuple:
            with self.runner_lock:
                if self.state["status"] in ("running", "paused") and self.worker_thread and self.worker_thread.is_alive():
                    return False, f"账号 {self.display_label} 已有任务正在运行中"
                if not self.state["logged_in"]:
                    return False, f"账号 {self.display_label} 未登录或凭证已失效"

                speed = max(1.0, min(float(speed or 1.5), 2.0))
                if not self.cached_courses:
                    try:
                        for c in self.client.get_course_list():
                            self.cached_courses[c["id"]] = c
                    except Exception as e:
                        return False, f"获取课程失败: {e}"

                targets = (
                    [self.cached_courses[cid] for cid in selected_ids if cid in self.cached_courses]
                    if selected_ids
                    else list(self.cached_courses.values())
                )
                if not targets:
                    return False, "未找到可学习的课程"

                self.stop_event.clear()
                self.pause_event.clear()
                self.state["speed"] = speed
                self.worker_thread = threading.Thread(
                    target=self._run_worker,
                    args=(targets, speed),
                    name=f"cx-{self.account_id}",
                    daemon=True,
                )
                self.worker_thread.start()
                return True, f"账号 {self.display_label} 已启动，共 {len(targets)} 门课程"

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
                return True, "running", "已继续"
            return False, self.state["status"], "当前无运行中任务"

        def stop(self) -> bool:
            if self.state["status"] in ("running", "paused"):
                self.stop_event.set()
                self.pause_event.clear()
                self.state["status"] = "stopped"
                self.state["action"] = "正在停止任务..."
                self.add_log("⏹ 已发送停止指令", "WARNING")
                return True
            return False

        def _run_worker(self, course_objs: List[Dict[str, Any]], speed: float) -> None:
            self.state["status"] = "running"
            self.state["speed"] = speed
            self.state["completed_tasks"] = 0
            self.state["skipped_quizzes"] = 0
            self.state["error_msg"] = ""
            self.state["progress"] = 0.0
            self.state["time_str"] = "--:-- / --:--"
            self.add_log(f"🚀 独立线程启动：共 {len(course_objs)} 门课程，倍速 {speed}x（自动跳过评分测验/作业）", "INFO")

            try:
                for c_idx, course in enumerate(course_objs, 1):
                    if self.stop_event.is_set():
                        break
                    title = course.get("title") or course.get("courseId")
                    self.state["current_course"] = {"id": course["id"], "title": title}
                    self.state["current_chapter"] = ""
                    self.state["current_task"] = None
                    self.state["action"] = f"正在读取课程章节 ({c_idx}/{len(course_objs)})..."
                    self.add_log(f"📚 [{c_idx}/{len(course_objs)}] 读取课程：{title}", "INFO")

                    try:
                        point_data = self.client.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
                    except Exception as e:
                        self.add_log(f"❌ 读取章节失败 ({title}): {e}", "ERROR")
                        continue

                    points = point_data.get("points") or []
                    self.add_log(f"📑 《{title}》共发现 {len(points)} 个章节节点", "INFO")

                    for pt in points:
                        if self.stop_event.is_set():
                            break
                        while self.pause_event.is_set() and not self.stop_event.is_set():
                            time.sleep(0.3)
                        if self.stop_event.is_set() or pt.get("has_finished"):
                            continue

                        pt_title = pt.get("title") or pt.get("id")
                        self.state["current_chapter"] = pt_title
                        self.state["action"] = f"检查章节任务点: {pt_title}"

                        try:
                            jobs, job_info = self.client.get_job_list(course, pt)
                        except Exception as e:
                            self.add_log(f"⚠️ 获取章节任务失败 ({pt_title}): {e}", "WARNING")
                            continue

                        if job_info.get("notOpen"):
                            self.add_log(f"🔒 章节未开放，跳过：{pt_title}", "WARNING")
                            continue
                        if not jobs:
                            continue

                        for job in jobs:
                            if self.stop_event.is_set():
                                break
                            while self.pause_event.is_set() and not self.stop_event.is_set():
                                time.sleep(0.3)
                            if self.stop_event.is_set():
                                break

                            jtype = job.get("type", "")
                            jname = job.get("name") or jtype
                            self.state["current_task"] = {"type": jtype, "name": jname}

                            if jtype == "workid":
                                self.state["skipped_quizzes"] += 1
                                self.add_log(f"⏭ 跳过评分测验/作业：{jname}（请手动在官网完成）", "WARNING")
                                continue

                            if jtype in ("video", "audio"):
                                is_audio = jtype == "audio"
                                label = "音频" if is_audio else "视频"
                                self.add_log(f"▶ 开始{label}任务：{jname}（章节：{pt_title}）", "INFO")

                                def _on_progress(cur: float, total: float, act: str) -> None:
                                    self.state["progress"] = round(min(100.0, 100.0 * cur / max(1.0, total)), 1)
                                    self.state["time_str"] = f"{_format_mmss(cur)} / {_format_mmss(total)}"
                                    self.state["action"] = f"{label}{act}"

                                res = self.client.study_video(
                                    course=course,
                                    job=job,
                                    job_info=job_info,
                                    speed=speed,
                                    progress_cb=_on_progress,
                                    pause_event=self.pause_event,
                                    stop_event=self.stop_event,
                                    is_audio=is_audio,
                                )
                                if res == "success":
                                    self.state["completed_tasks"] += 1
                                    self.add_log(f"✅ 完成{label}：{jname}", "INFO")
                                elif res == "captcha":
                                    self.pause_event.set()
                                    self.state["status"] = "paused"
                                    self.state["action"] = "⚠️ 触发安全验证，请在浏览器完成验证后继续"
                                    self.add_log("⚠️ 触发学习通安全验证，本账号已自动暂停；请先在官网手动验证后再继续。", "ERROR")
                                    while self.pause_event.is_set() and not self.stop_event.is_set():
                                        time.sleep(0.4)
                                elif res == "forbidden":
                                    self.add_log(f"⚠️ 上报返回 403，跳过当前任务：{jname}", "WARNING")
                                else:
                                    self.add_log(f"❌ {label}任务执行失败：{jname}", "ERROR")
                            elif jtype == "document":
                                self.state["action"] = f"正在完成文档任务: {jname}"
                                if self.client.study_document(course, job) == "success":
                                    self.state["completed_tasks"] += 1
                                    self.add_log(f"✅ 完成文档任务：{jname}", "INFO")
                            elif jtype == "read":
                                self.state["action"] = f"正在完成阅读任务: {jname}"
                                if self.client.study_read(course, job, job_info) == "success":
                                    self.state["completed_tasks"] += 1
                                    self.add_log(f"✅ 完成阅读任务：{jname}", "INFO")

                if self.stop_event.is_set():
                    self.state["status"] = "stopped"
                    self.state["action"] = "任务已安全停止"
                    self.add_log("⏹ 任务已停止", "WARNING")
                else:
                    self.state["status"] = "idle"
                    self.state["progress"] = 100.0
                    self.state["action"] = "所选课程视频/文档/阅读任务已全部扫描完成！"
                    self.add_log(f"🎉 全部课程处理完成！完成 {self.state['completed_tasks']} 个任务点，跳过 {self.state['skipped_quizzes']} 个测验。", "INFO")
            except InterruptedError:
                self.state["status"] = "stopped"
                self.state["action"] = "任务已手动停止"
                self.add_log("⏹ 任务已手动停止", "WARNING")
            except Exception as e:
                self.state["status"] = "error"
                self.state["error_msg"] = str(e)
                self.state["action"] = f"执行异常: {e}"
                self.add_log(f"❌ 后台线程异常: {e}", "ERROR")

    accounts: Dict[str, AccountSession] = {}

    def save_accounts_store() -> bool:
        with accounts_lock:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(accounts_file)), exist_ok=True)
                payload = {aid: acc.to_persist_dict() for aid, acc in accounts.items()}
                tmp_path = accounts_file + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, accounts_file)
                return True
            except Exception as e:
                add_global_log(f"保存多账号配置失败: {e}", "ERROR")
                return False

    def register_or_update_account(cookies_list, uid="", user_name="", remark="") -> AccountSession:
        temp_client = ChaoxingClient()
        temp_client.load_cookies(cookies_list)
        real_uid = str(uid or temp_client.get_uid() or "").strip()
        if not real_uid:
            raise ValueError("未能从登录凭证中读取用户 UID")
        real_name = (user_name or temp_client.get_name() or f"UID {real_uid}").strip()

        with accounts_lock:
            existing = accounts.get(real_uid)
            if existing:
                existing.client.load_cookies(cookies_list)
                existing.state["logged_in"] = True
                existing.state["user_name"] = real_name
                if remark:
                    existing.state["remark"] = remark.strip()
                existing.cached_courses.clear()
                acc = existing
            else:
                acc = AccountSession(
                    account_id=real_uid,
                    user_name=real_name,
                    remark=remark.strip() if remark else "",
                    cookies=cookies_list,
                )
                acc.state["logged_in"] = True
                accounts[real_uid] = acc

        save_accounts_store()
        return acc

    if os.path.exists(accounts_file):
        try:
            with open(accounts_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for aid, item in raw.items():
                    if not isinstance(item, dict):
                        continue
                    acc = AccountSession(
                        account_id=str(item.get("account_id") or aid),
                        user_name=str(item.get("user_name") or ""),
                        remark=str(item.get("remark") or ""),
                        speed=float(item.get("speed") or 1.5),
                        cookies=item.get("cookies") or [],
                    )
                    if acc.client.validate_session():
                        acc.state["logged_in"] = True
                        real_name = acc.client.get_name()
                        if real_name:
                            acc.state["user_name"] = real_name
                        acc.add_log("已恢复本地持久化学习通会话", "INFO")
                    else:
                        acc.state["logged_in"] = False
                        acc.state["action"] = "登录凭证已过期，请重新登录"
                    accounts[acc.account_id] = acc
        except Exception as e:
            add_global_log(f"加载多账号文件失败: {e}", "WARNING")

    def _get_target(account_id: Optional[str] = None) -> Optional[AccountSession]:
        with accounts_lock:
            if account_id and str(account_id) in accounts:
                return accounts[str(account_id)]
            if not account_id and accounts:
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
        add_global_log(f"🗑 已移除账号：{label}", "WARNING")
        return jsonify({"code": 200, "ok": True, "msg": f"已移除账号 {label}"})

    @app.route("/api/status")
    def api_status():
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
            for k in ("status", "logged_in", "user_name", "user_uid", "current_course", "current_chapter", "current_task", "progress", "time_str", "speed", "action", "completed_tasks", "skipped_quizzes", "error_msg", "logs"):
                resp[k] = active_detail[k]
        else:
            resp.update({"status": "idle", "logged_in": False, "user_name": "", "user_uid": "", "progress": 0.0, "time_str": "--:-- / --:--", "speed": 1.5, "action": "请先添加学习通账号", "completed_tasks": 0, "skipped_quizzes": 0, "logs": list(global_logs)[-120:]})
        return jsonify(resp)

    @app.route("/api/qr")
    def api_qr():
        try:
            return jsonify({"code": 200, "ok": True, **qr_auth_client.create_qr()})
        except Exception as e:
            return jsonify({"code": 500, "ok": False, "msg": f"获取二维码失败: {e}"}), 500

    @app.route("/api/qr/poll")
    def api_qr_poll():
        uuid_val = (request.args.get("uuid") or "").strip()
        enc_val = (request.args.get("enc") or "").strip()
        remark = (request.args.get("remark") or "").strip()
        if not uuid_val or not enc_val:
            return jsonify({"code": 400, "status": "error", "msg": "缺少 uuid 或 enc 参数"}), 400
        try:
            res = qr_auth_client.poll_qr(uuid_val, enc_val)
            if res.get("status") == "success":
                acc = register_or_update_account(res.get("cookies") or [], res.get("uid") or "", res.get("name") or "", remark)
                acc.add_log("📱 学习通扫码登录成功，已加入多账号池", "INFO")
                return jsonify({"status": "success", "msg": f"扫码登录成功 ({acc.display_label})", "account_id": acc.account_id, "account": acc.to_summary()})
            return jsonify(res)
        except Exception as e:
            return jsonify({"status": "error", "msg": str(e)}), 500

    @app.route("/api/login", methods=["POST"])
    def api_login_password():
        data = request.json or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        remark = (data.get("remark") or "").strip()
        if not username or not password:
            return jsonify({"code": 400, "ok": False, "msg": "手机号和密码不能为空"}), 400
        try:
            temp = ChaoxingClient()
            ok, msg = temp.login_password(username, password)
            if not ok:
                return jsonify({"code": 401, "ok": False, "msg": msg}), 401
            acc = register_or_update_account(temp.export_cookies(), temp.get_uid(), temp.get_name() or username, remark)
            acc.add_log("🔐 账号密码登录成功，已加入多账号池", "INFO")
            return jsonify({"code": 200, "ok": True, "msg": msg, "account_id": acc.account_id, "user_name": acc.state["user_name"], "account": acc.to_summary()})
        except Exception as e:
            return jsonify({"code": 500, "ok": False, "msg": f"登录请求异常: {e}"}), 500

    @app.route("/api/cookie", methods=["POST"])
    def api_set_cookie():
        data = request.json or {}
        raw = (data.get("cookie") or "").strip()
        remark = (data.get("remark") or "").strip()
        if not raw:
            return jsonify({"code": 400, "ok": False, "msg": "Cookie 不能为空"}), 400
        try:
            temp = ChaoxingClient()
            temp.set_cookie_string(raw)
            if not temp.validate_session():
                return jsonify({"code": 401, "ok": False, "msg": "Cookie 校验未通过"}), 401
            acc = register_or_update_account(temp.export_cookies(), temp.get_uid(), temp.get_name(), remark)
            acc.add_log("🍪 Cookie 导入成功，已加入多账号池", "INFO")
            return jsonify({"code": 200, "ok": True, "msg": f"Cookie 添加成功 ({acc.display_label})", "account_id": acc.account_id, "user_name": acc.state["user_name"], "account": acc.to_summary()})
        except Exception as e:
            return jsonify({"code": 500, "ok": False, "msg": f"Cookie 解析失败: {e}"}), 500

    @app.route("/api/courses")
    def api_courses():
        acc = _get_target((request.args.get("account_id") or "").strip())
        if not acc or not acc.state["logged_in"]:
            return jsonify({"code": 401, "ok": False, "msg": "请先添加并登录学习通账号", "courses": []}), 401
        try:
            courses = acc.client.get_course_list()
            acc.cached_courses = {c["id"]: c for c in courses}
            return jsonify({"code": 200, "ok": True, "account_id": acc.account_id, "user_name": acc.state["user_name"], "courses": courses, "count": len(courses)})
        except Exception as e:
            return jsonify({"code": 500, "ok": False, "msg": f"获取课程列表失败: {e}", "courses": []}), 500

    @app.route("/api/start", methods=["POST"])
    def api_start():
        data = request.json or {}
        acc = _get_target((data.get("account_id") or "").strip())
        if not acc:
            return jsonify({"code": 404, "ok": False, "msg": "未找到目标账号"}), 404
        ok, msg = acc.start_courses(data.get("course_ids") or [], float(data.get("speed", acc.state.get("speed", 1.5))))
        save_accounts_store()
        return jsonify({"code": 200 if ok else 400, "ok": ok, "msg": msg, "account_id": acc.account_id}), (200 if ok else 400)

    @app.route("/api/pause", methods=["POST"])
    def api_pause():
        acc = _get_target(((request.json or {}).get("account_id") or "").strip())
        if not acc:
            return jsonify({"code": 404, "ok": False, "msg": "账号不存在"}), 404
        ok, status, msg = acc.toggle_pause()
        return jsonify({"code": 200 if ok else 400, "ok": ok, "status": status, "msg": msg}), (200 if ok else 400)

    @app.route("/api/stop", methods=["POST"])
    def api_stop():
        acc = _get_target(((request.json or {}).get("account_id") or "").strip())
        if not acc:
            return jsonify({"code": 404, "ok": False, "msg": "账号不存在"}), 404
        ok = acc.stop()
        return jsonify({"code": 200 if ok else 400, "ok": ok, "msg": "停止指令已发送" if ok else "当前无运行任务"}), (200 if ok else 400)

    @app.route("/api/start_all", methods=["POST"])
    def api_start_all():
        speed = max(1.0, min(float((request.json or {}).get("speed", 1.5)), 2.0))
        started, skipped = [], []
        with accounts_lock:
            snap = list(accounts.values())
        for acc in snap:
            if not acc.state["logged_in"]:
                skipped.append(f"{acc.display_label}(未登录)")
                continue
            if acc.state["status"] in ("running", "paused") and acc.worker_thread and acc.worker_thread.is_alive():
                skipped.append(f"{acc.display_label}(已在运行)")
                continue
            ok, msg = acc.start_courses([], speed)
            (started if ok else skipped).append(acc.display_label if ok else f"{acc.display_label}({msg})")
        save_accounts_store()
        return jsonify({"code": 200, "ok": True, "started": started, "skipped": skipped, "msg": f"已同时启动 {len(started)} 个账号"})

    @app.route("/api/pause_all", methods=["POST"])
    def api_pause_all():
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
    def api_stop_all():
        affected = 0
        with accounts_lock:
            for acc in accounts.values():
                if acc.stop():
                    affected += 1
        return jsonify({"code": 200, "ok": True, "affected": affected, "msg": f"已停止 {affected} 个账号"})

    @app.route("/api/assistant/config", methods=["GET", "POST"])
    def api_assistant_config():
        if request.method == "GET":
            return jsonify({"code": 200, "ok": True, "config": get_public_ai_config()})
        try:
            saved = save_ai_config(request.json or {})
            return jsonify({"code": 200, "ok": True, "msg": "AI 配置已保存（三平台共用）", "config": saved})
        except Exception as e:
            return jsonify({"code": 500, "ok": False, "msg": f"保存配置失败: {e}"}), 500

    @app.route("/api/assistant/ask", methods=["POST"])
    def api_assistant_ask():
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
