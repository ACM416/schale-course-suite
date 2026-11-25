# -*- coding: utf-8 -*-
import json
import os
from flask import Flask, jsonify, render_template, request

from core.paths import get_data_dir, get_template_dir
from core.study_assistant import ask_assistant, get_public_ai_config, save_ai_config
from platforms.uooc.uooc_engine import UoocClient
from platforms.uooc.uooc_runner import TaskManager


def _config_path() -> str:
    return str(get_data_dir() / "uooc_config.json")


def load_config():
    path = _config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"cookie": "", "speed": 1.5, "interval": 15}


def save_config(cfg):
    path = _config_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def create_app() -> Flask:
    app = Flask("uooc_console", template_folder=get_template_dir("uooc"))
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    runner = TaskManager()

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/config", methods=["GET"])
    def get_config():
        return jsonify(load_config())

    @app.route("/api/cookie", methods=["POST"])
    def set_cookie():
        data = request.json or {}
        cookie = data.get("cookie", "").strip()
        if not cookie:
            return jsonify({"code": 0, "msg": "Cookie 不能为空"})
        client = UoocClient(cookie)
        ok, res = client.get_user()
        if not ok:
            return jsonify({"code": 0, "msg": f"Cookie 校验失败: {res}"})
        cfg = load_config()
        cfg["cookie"] = cookie
        save_config(cfg)
        return jsonify({"code": 1, "msg": "Cookie 校验并保存成功", "user": res})

    @app.route("/api/user", methods=["GET"])
    def get_user():
        cookie = load_config().get("cookie", "")
        if not cookie:
            return jsonify({"code": 0, "msg": "尚未设置 Cookie"})
        client = UoocClient(cookie)
        ok, res = client.get_user()
        if not ok:
            return jsonify({"code": 0, "msg": f"获取用户信息失败: {res}"})
        return jsonify({"code": 1, "user": res})

    @app.route("/api/courses", methods=["GET"])
    def get_courses():
        cookie = load_config().get("cookie", "")
        if not cookie:
            return jsonify({"code": 0, "msg": "尚未设置 Cookie"})
        client = UoocClient(cookie)
        ok, courses = client.get_courses()
        if not ok:
            return jsonify({"code": 0, "msg": f"获取课程失败: {courses}"})
        return jsonify({"code": 1, "courses": courses})

    @app.route("/api/start", methods=["POST"])
    def start_task():
        data = request.json or {}
        cids = data.get("cids", [])
        speed = float(data.get("speed", 1.5))
        interval = int(data.get("interval", 15))
        cfg = load_config()
        cookie = cfg.get("cookie", "")
        if not cookie:
            return jsonify({"code": 0, "msg": "请先配置并保存有效 Cookie"})
        if not cids:
            client = UoocClient(cookie)
            ok, courses = client.get_courses()
            if not ok or not courses:
                return jsonify({"code": 0, "msg": "未找到任何可用课程"})
            cids = [c["cid"] for c in courses if c.get("cid")]
        cfg["speed"] = speed
        cfg["interval"] = interval
        save_config(cfg)
        ok, msg = runner.start_courses(cookie, cids, speed, interval)
        return jsonify({"code": 1 if ok else 0, "msg": "刷课任务已在后台启动" if ok else msg})

    @app.route("/api/stop", methods=["POST"])
    def stop_task():
        runner.stop()
        return jsonify({"code": 1, "msg": "已发送停止请求"})

    @app.route("/api/pause", methods=["POST"])
    def pause_task():
        runner.pause()
        return jsonify({"code": 1, "msg": "状态已切换"})

    @app.route("/api/status", methods=["GET"])
    def get_status():
        return jsonify(runner.get_state())

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
