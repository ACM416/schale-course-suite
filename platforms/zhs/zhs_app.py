# -*- coding: utf-8 -*-
import json
import logging
import os
import re
import sys
import threading
import time
from collections import deque

import requests
from flask import Flask, jsonify, render_template, request

from core.paths import get_bundle_dir, get_data_dir, get_template_dir
from core.study_assistant import ask_assistant, get_public_ai_config, save_ai_config

ZHS_DIR = str(get_bundle_dir() / "platforms" / "zhs")
if ZHS_DIR not in sys.path:
    sys.path.insert(0, ZHS_DIR)

import fucker  # noqa: E402
import utils  # noqa: E402
from fucker import Fucker  # noqa: E402
from utils import cookie_jar_to_list  # noqa: E402


def create_app() -> Flask:
    app = Flask("zhs_console", template_folder=get_template_dir("zhs"))
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    cookies_file = str(get_data_dir() / "zhs_cookies.json")
    log_buffer = deque(maxlen=300)

    state = {
        "status": "idle",
        "logged_in": False,
        "user_info": "",
        "current_course": None,
        "current_video": None,
        "progress": 0.0,
        "time_str": "--:-- / --:--",
        "speed": 1.5,
        "action": "等待指令",
        "completed_videos": 0,
        "error_msg": "",
    }

    pause_event = threading.Event()
    stop_event = threading.Event()
    runner_lock = threading.Lock()
    worker_thread = None

    class BufferingLogHandler(logging.Handler):
        def emit(self, record):
            try:
                log_buffer.append(
                    {
                        "time": time.strftime("%H:%M:%S"),
                        "level": record.levelname,
                        "msg": self.format(record),
                    }
                )
            except Exception:
                pass

    log_handler = BufferingLogHandler()
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    fucker.logger.addHandler(log_handler)
    fucker.logger.setLevel("INFO")

    def add_log(msg: str, level: str = "INFO"):
        log_buffer.append({"time": time.strftime("%H:%M:%S"), "level": level, "msg": msg})

    fucker_inst = Fucker(speed=1.5, end_thre=1.0, tree_view=True, progressbar_view=True)

    orig_progressBar = utils.progressBar

    def hooked_progressBar(iteration, total, prefix="", suffix="", decimals=1, length=None, fill="#", progressbar_view=True):
        try:
            if total and total > 0:
                state["progress"] = min(round(100.0 * float(iteration) / float(total), 1), 100.0)
                cur_m, cur_s = divmod(int(iteration), 60)
                tot_m, tot_s = divmod(int(total), 60)
                state["time_str"] = f"{cur_m:02d}:{cur_s:02d} / {tot_m:02d}:{tot_s:02d}"
            if prefix:
                if "pause" in prefix.lower():
                    state["action"] = "微休避检中 (模拟真人暂停)"
                elif "quiz" in prefix.lower() or "answering" in prefix.lower():
                    state["action"] = "自动处理随堂弹题中..."
                else:
                    state["action"] = "视频播放学习中"
        except Exception:
            pass
        return orig_progressBar(iteration, total, prefix, suffix, decimals, length, fill, False)

    utils.progressBar = hooked_progressBar
    fucker.progressBar = hooked_progressBar

    orig_sleep = time.sleep

    def hooked_sleep(seconds):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if stop_event.is_set():
                raise InterruptedError("用户手动停止任务")
            while pause_event.is_set():
                if stop_event.is_set():
                    raise InterruptedError("用户手动停止任务")
                orig_sleep(0.2)
            orig_sleep(min(0.2, max(0.01, deadline - time.time())))

    fucker.time.sleep = hooked_sleep

    def save_cookies_to_file(cookies_obj):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(cookies_file)), exist_ok=True)
            with open(cookies_file, "w", encoding="utf-8") as f:
                if hasattr(cookies_obj, "__iter__") and not isinstance(cookies_obj, (list, dict)):
                    json.dump(cookie_jar_to_list(cookies_obj), f, indent=2, ensure_ascii=False)
                else:
                    json.dump(cookies_obj, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            add_log(f"保存 Cookie 失败: {e}", "ERROR")
            return False

    if os.path.exists(cookies_file):
        try:
            with open(cookies_file, "r", encoding="utf-8") as f:
                c_data = json.load(f)
            if c_data:
                fucker_inst.cookies = c_data
                state["logged_in"] = True
                add_log("已加载本地持久化登录凭证", "INFO")
        except Exception as e:
            add_log(f"本地凭证加载失败: {e}", "WARNING")

    orig_fuckZhidaoVideo = fucker_inst.fuckZhidaoVideo

    def wrapped_fuckZhidaoVideo(RAC_id, video_id):
        try:
            ctx = fucker_inst.getZhidaoContext(RAC_id)
            video = ctx.videos.get(video_id)
            vname = video.name if video else f"小节 #{video_id}"
            state["current_video"] = {"id": str(video_id), "name": vname}
            state["action"] = f"正在学习: {vname}"
            add_log(f"▶ 开始小节: {vname}", "INFO")
        except Exception:
            state["current_video"] = {"id": str(video_id), "name": f"小节 #{video_id}"}
        res = orig_fuckZhidaoVideo(RAC_id, video_id)
        state["completed_videos"] += 1
        return res

    fucker_inst.fuckZhidaoVideo = wrapped_fuckZhidaoVideo

    orig_fuckHikeVideo = fucker_inst.fuckHikeVideo

    def wrapped_fuckHikeVideo(course_id, file_id, prev_time=0):
        try:
            file_info = fucker_inst.stuViewFile(course_id, file_id)
            vname = file_info.name if file_info and file_info.name else f"课件 #{file_id}"
            state["current_video"] = {"id": str(file_id), "name": vname}
            state["action"] = f"正在学习: {vname}"
            add_log(f"▶ 开始校内课件: {vname}", "INFO")
        except Exception:
            state["current_video"] = {"id": str(file_id), "name": f"课件 #{file_id}"}
        res = orig_fuckHikeVideo(course_id, file_id, prev_time)
        state["completed_videos"] += 1
        return res

    fucker_inst.fuckHikeVideo = wrapped_fuckHikeVideo

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/status")
    def get_status():
        return jsonify({**state, "logs": list(log_buffer)[-100:]})

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
        token = request.args.get("token")
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
                sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"})
                sess.get(login_page, params={"pwd": r.get("oncePassword")}, timeout=10)
                fucker_inst.cookies = sess.cookies.copy()
                save_cookies_to_file(sess.cookies)
                state["logged_in"] = True
                add_log("扫码登录成功！凭证已自动持久化存储", "INFO")
                return jsonify({"status": "success", "msg": "登录成功！"})
            if status == 2:
                return jsonify({"status": "expired", "msg": "二维码已失效，请刷新"})
            if status == 3:
                return jsonify({"status": "canceled", "msg": "手机端已取消登录"})
            return jsonify({"status": "error", "msg": f"未知状态: {r.get('msg', '')}"})
        except Exception as e:
            return jsonify({"status": "error", "msg": str(e)}), 500

    @app.route("/api/cookie", methods=["POST"])
    def set_cookie():
        cookie_str = ((request.json or {}).get("cookie") or "").strip()
        if not cookie_str:
            return jsonify({"code": 400, "msg": "Cookie 不能为空"}), 400
        try:
            if cookie_str.startswith("[") or cookie_str.startswith("{"):
                fucker_inst.cookies = json.loads(cookie_str)
                save_cookies_to_file(fucker_inst.cookies)
            else:
                jar = requests.cookies.RequestsCookieJar()
                for part in cookie_str.split(";"):
                    if "=" in part:
                        k, v = part.strip().split("=", 1)
                        jar.set(k, v, domain=".zhihuishu.com")
                fucker_inst.cookies = jar
                save_cookies_to_file(jar)
            state["logged_in"] = True
            add_log("手动 Cookie 凭证导入成功并持久化", "INFO")
            return jsonify({"code": 200, "msg": "Cookie 导入成功！"})
        except Exception as e:
            return jsonify({"code": 500, "msg": f"Cookie 解析/应用失败: {e}"}), 500

    @app.route("/api/courses")
    def get_courses():
        if not state["logged_in"]:
            return jsonify({"code": 401, "msg": "请先登录", "courses": []}), 401
        course_list = []
        errors = []
        try:
            fucker_inst.courses.zhidao = None
            for c in (fucker_inst.getZhidaoList() or []):
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
            fucker_inst.courses.hike = None
            for c in (fucker_inst.getHikeList() or []):
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
        if not course_list and errors:
            return jsonify({"code": 500, "msg": "; ".join(errors), "courses": []}), 500
        return jsonify({"code": 200, "courses": course_list, "count": len(course_list)})

    def background_runner(course_ids, speed):
        state["status"] = "running"
        state["speed"] = speed
        state["completed_videos"] = 0
        state["error_msg"] = ""
        fucker_inst.speed = speed
        add_log(f"🚀 任务启动: 共 {len(course_ids)} 门课程，播放倍速设定为 {speed}x", "INFO")
        try:
            for idx, cid in enumerate(course_ids, 1):
                if stop_event.is_set():
                    break
                c_type = "zhidao" if re.match(r".*[a-zA-Z].*", str(cid)) else "hike"
                state["current_course"] = {"id": str(cid), "name": f"课程 #{cid}", "type": c_type}
                state["action"] = f"正在载入课程 ({idx}/{len(course_ids)}): {cid}"
                add_log(f"📚 开始执行课程 [{idx}/{len(course_ids)}]: {cid} ({c_type})", "INFO")
                try:
                    fucker_inst.fuckCourse(str(cid))
                    add_log(f"✅ 课程已全部学完: {cid}", "INFO")
                except InterruptedError:
                    add_log("⏹ 任务已被用户停止", "WARNING")
                    break
                except Exception as e:
                    add_log(f"❌ 课程执行出错 ({cid}): {e}", "ERROR")

            if stop_event.is_set():
                state["status"] = "stopped"
                state["action"] = "任务已停止"
            else:
                state["status"] = "idle"
                state["action"] = "所有选中课程均已完成！"
                state["progress"] = 100.0
                add_log("🎉 全部选定网课学习流程已完成！", "INFO")
        except Exception as e:
            state["status"] = "error"
            state["error_msg"] = str(e)
            state["action"] = f"异常终止: {e}"
            add_log(f"后台线程异常: {e}", "ERROR")

    @app.route("/api/start", methods=["POST"])
    def start_task():
        nonlocal worker_thread
        with runner_lock:
            if state["status"] == "running":
                return jsonify({"code": 400, "msg": "已有任务正在运行中"}), 400
            data = request.json or {}
            course_ids = data.get("course_ids", [])
            speed = min(1.5, max(1.0, float(data.get("speed", 1.5))))
            if not course_ids:
                try:
                    z = [c.secret for c in (fucker_inst.getZhidaoList() or [])]
                    h = [str(c.courseId) for c in (fucker_inst.getHikeList() or [])]
                    course_ids = z + h
                except Exception as e:
                    return jsonify({"code": 400, "msg": f"获取课程失败: {e}"}), 400
            if not course_ids:
                return jsonify({"code": 400, "msg": "未发现可学习的课程"}), 400
            stop_event.clear()
            pause_event.clear()
            worker_thread = threading.Thread(target=background_runner, args=(course_ids, speed), daemon=True)
            worker_thread.start()
            return jsonify({"code": 200, "msg": f"刷课任务已启动，共 {len(course_ids)} 门课程", "count": len(course_ids)})

    @app.route("/api/pause", methods=["POST"])
    def toggle_pause():
        if state["status"] == "running":
            pause_event.set()
            state["status"] = "paused"
            state["action"] = "任务已暂停"
            add_log("⏸ 任务已暂停", "WARNING")
            return jsonify({"code": 200, "status": "paused", "msg": "已暂停"})
        if state["status"] == "paused":
            pause_event.clear()
            state["status"] = "running"
            state["action"] = "任务继续运行"
            add_log("▶ 任务已恢复运行", "INFO")
            return jsonify({"code": 200, "status": "running", "msg": "已恢复运行"})
        return jsonify({"code": 400, "msg": "当前没有正在运行的任务"}), 400

    @app.route("/api/stop", methods=["POST"])
    def stop_task():
        if state["status"] in ("running", "paused"):
            stop_event.set()
            pause_event.clear()
            state["status"] = "stopped"
            state["action"] = "正在停止任务..."
            add_log("⏹ 正在停止任务...", "WARNING")
            return jsonify({"code": 200, "msg": "停止指令已发送"})
        return jsonify({"code": 400, "msg": "没有正在运行的任务"}), 400

    @app.route("/api/logout", methods=["POST"])
    def logout():
        if os.path.exists(cookies_file):
            os.remove(cookies_file)
        fucker_inst._cookies = None
        state["logged_in"] = False
        add_log("已清除持久化登录凭证", "INFO")
        return jsonify({"code": 200, "msg": "已退出登录并清除凭证"})

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
