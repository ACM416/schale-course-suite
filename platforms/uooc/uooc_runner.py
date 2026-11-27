# -*- coding: utf-8 -*-
import logging
import threading
import time
from datetime import datetime
from typing import Callable, Optional

try:
    from platforms.uooc.uooc_engine import UoocClient
except ImportError:
    from engine import UoocClient

logger = logging.getLogger("uooc_runner")


class TaskManager:
    def __init__(self, account_label: str = "", global_log_cb: Optional[Callable[[dict], None]] = None):
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.stop_requested = False
        self.pause_requested = False
        self.account_label = account_label
        self.global_log_cb = global_log_cb

        self.state = {
            "status": "idle",
            "running": False,
            "paused": False,
            "action": "等待指令",
            "current_course_name": "",
            "current_course_id": None,
            "current_video_title": "",
            "current_pos": 0.0,
            "current_duration": 0.0,
            "progress_percent": 0.0,
            "speed": 1.5,
            "interval": 15,
            "stats": {
                "total_courses": 0,
                "current_course_index": 0,
                "total_videos": 0,
                "completed_videos": 0,
                "skipped_videos": 0,
                "locked_videos": 0,
            },
            "logs": [],
        }

    def log(self, msg: str, level: str = "INFO"):
        now_str = datetime.now().strftime("%H:%M:%S")
        entry = {"time": now_str, "level": level, "account": self.account_label, "msg": msg}
        with self.lock:
            self.state["logs"].append(entry)
            if len(self.state["logs"]) > 250:
                self.state["logs"].pop(0)
        if self.global_log_cb:
            try:
                self.global_log_cb(entry)
            except Exception:
                pass
        prefix = f"[{self.account_label}] " if self.account_label else ""
        logger.info(f"[{entry['time']}] [{level}] {prefix}{msg}")

    def get_state(self):
        with self.lock:
            return {
                **self.state,
                "stats": dict(self.state["stats"]),
                "logs": list(self.state["logs"][-120:]),
            }

    def stop(self) -> bool:
        with self.lock:
            if not self.state["running"]:
                return False
            self.stop_requested = True
            self.pause_requested = False
            self.state["paused"] = False
            self.state["status"] = "stopped"
            self.state["action"] = "正在安全停止..."
        self.log("⏹ 收到停止指令，正在安全退出当前循环...", "WARN")
        return True

    def pause(self):
        with self.lock:
            if not self.state["running"]:
                return False, self.state["status"], "当前无运行中任务"
            self.pause_requested = not self.pause_requested
            self.state["paused"] = self.pause_requested
            self.state["status"] = "paused" if self.pause_requested else "running"
            self.state["action"] = "任务已暂停" if self.pause_requested else "恢复播放推进中"
            paused = self.pause_requested
        status_zh = "暂停" if paused else "继续"
        self.log(f"{'⏸' if paused else '▶'} 任务已{status_zh}", "WARN" if paused else "INFO")
        return True, ("paused" if paused else "running"), f"已{status_zh}"

    def start_courses(self, cookie: str, cid_list: list, speed: float = 1.5, interval: int = 15):
        with self.lock:
            if self.state["running"] and self.thread and self.thread.is_alive():
                return False, "该账号已有刷课任务正在进行中"
            self.state["running"] = True
            self.state["paused"] = False
            self.state["status"] = "running"
            self.state["action"] = "启动刷课引擎..."
            self.state["speed"] = speed
            self.state["interval"] = interval
            self.state["progress_percent"] = 0.0
            self.state["current_pos"] = 0.0
            self.state["current_duration"] = 0.0
            self.state["stats"] = {
                "total_courses": len(cid_list),
                "current_course_index": 0,
                "total_videos": 0,
                "completed_videos": 0,
                "skipped_videos": 0,
                "locked_videos": 0,
            }
            self.stop_requested = False
            self.pause_requested = False

        self.thread = threading.Thread(
            target=self._run_loop,
            args=(cookie, list(cid_list), speed, interval),
            name=f"uooc-{self.account_label or 'worker'}",
            daemon=True,
        )
        self.thread.start()
        return True, f"任务已启动，共 {len(cid_list)} 门课程"

    def _sleep_check(self, seconds: float):
        step = 0.5
        elapsed = 0.0
        while elapsed < seconds:
            if self.stop_requested:
                return False
            while self.pause_requested:
                if self.stop_requested:
                    return False
                time.sleep(0.5)
            time.sleep(step)
            elapsed += step
        return True

    def _extract_source_url(self, item: dict) -> str:
        play_list = item.get("video_play_list") or []
        for p in play_list:
            if isinstance(p, dict) and p.get("source"):
                return p["source"].strip()
            if isinstance(p, str) and p.startswith("http"):
                return p.strip()

        video_url = item.get("video_url")
        if isinstance(video_url, dict):
            for _, v in video_url.items():
                if isinstance(v, dict) and v.get("source"):
                    return v["source"].strip()
                if isinstance(v, str) and v.startswith("http"):
                    return v.strip()
        elif isinstance(video_url, str) and video_url.startswith("http"):
            return video_url.strip()
        return ""

    def _discover_tasks(self, client: UoocClient, cid: int, catalog: list):
        tasks = []
        gate_locked = False

        def walk(chapter_id, section_id, node, is_root_section=False):
            nonlocal gate_locked
            subsection_id = 0 if is_root_section else node.get("id", 0)

            ok, items = client.get_unit_learn(cid, chapter_id, section_id, subsection_id)
            if not ok:
                if str(items).startswith("GATE_LOCKED"):
                    self.log(f"⚠️ 遇到闯关限制章节: {items}", "WARN")
                    gate_locked = True
                    return False
                return True

            for item in items:
                source_url = self._extract_source_url(item)
                if not source_url:
                    continue

                finished = item.get("finished", 0)
                try:
                    video_pos = float(item.get("video_pos", 0) or 0)
                except Exception:
                    video_pos = 0.0

                tasks.append(
                    {
                        "cid": cid,
                        "chapter_id": chapter_id,
                        "section_id": section_id,
                        "subsection_id": subsection_id,
                        "resource_id": item.get("id"),
                        "title": item.get("title") or "未命名视频",
                        "finished": finished,
                        "video_pos": video_pos,
                        "source_url": source_url,
                    }
                )

            children = node.get("children") or []
            for child in children:
                if not walk(chapter_id, section_id, child, is_root_section=False):
                    return False
            return True

        for chapter in catalog:
            chap_id = chapter.get("id", 0)
            sections = chapter.get("children") or []
            for sec in sections:
                sec_id = sec.get("id", 0)
                sub_children = sec.get("children") or []
                if sub_children:
                    for sub in sub_children:
                        if not walk(chap_id, sec_id, sub, is_root_section=False):
                            break
                else:
                    walk(chap_id, sec_id, sec, is_root_section=True)
                if gate_locked:
                    break
            if gate_locked:
                break

        return tasks, gate_locked

    def _run_loop(self, cookie: str, cid_list: list, speed: float, interval: int):
        try:
            client = UoocClient(cookie)
            self.log(f"🚀 独立线程启动，目标课程: {len(cid_list)} 门，倍速: {speed}x，间隔: {interval}s")

            ok, user = client.get_user()
            if not ok:
                self.log(f"❌ 登录认证失败: {user}", "ERROR")
                with self.lock:
                    self.state["running"] = False
                    self.state["status"] = "error"
                    self.state["action"] = f"认证失败: {user}"
                return

            self.log(f"👤 当前认证用户: {user.get('name') or user.get('nick')} (UID: {user.get('id')})")

            ok, all_courses = client.get_courses()
            course_name_map = {}
            if ok:
                for c in all_courses:
                    course_name_map[str(c["cid"])] = c["name"]

            for idx, cid in enumerate(cid_list):
                if self.stop_requested:
                    break

                c_name = course_name_map.get(str(cid), f"课程 {cid}")
                with self.lock:
                    self.state["current_course_name"] = c_name
                    self.state["current_course_id"] = cid
                    self.state["stats"]["current_course_index"] = idx + 1
                    self.state["action"] = f"解析课程 ({idx + 1}/{len(cid_list)}): {c_name}"

                self.log(f"📚 开始处理课程 [{idx + 1}/{len(cid_list)}]: {c_name} (CID: {cid})")

                progress = client.get_course_progress(cid)
                self.log(f"📊 课程初始视频进度: {progress.get('video_progress', '0%')}")
                if progress.get("video_progress_value", 0) >= 100 or progress.get("video_progress") == "100%":
                    self.log("🎉 该课程视频进度已达 100%，自动跳过", "SUCCESS")
                    continue

                ok, catalog = client.get_catalog(cid)
                if not ok or not catalog:
                    self.log(f"⚠️ 无法获取该课程目录: {catalog}", "WARN")
                    continue

                self.log("🔍 正在解析课程目录树并收集待学视频...")
                tasks, _ = self._discover_tasks(client, cid, catalog)
                self.log(f"📋 共扫描到 {len(tasks)} 个视频资源")

                unwatched = [t for t in tasks if t["finished"] != 1]
                with self.lock:
                    self.state["stats"]["total_videos"] += len(unwatched)
                self.log(f"⏳ 待学习视频: {len(unwatched)} 个，已完成: {len(tasks) - len(unwatched)} 个")

                for v_idx, task in enumerate(unwatched):
                    if self.stop_requested:
                        break

                    with self.lock:
                        self.state["current_video_title"] = task["title"]
                        self.state["current_pos"] = task["video_pos"]
                        self.state["current_duration"] = 0.0
                        self.state["progress_percent"] = 0.0
                        self.state["action"] = f"播放 [{v_idx + 1}/{len(unwatched)}]: {task['title']}"

                    self.log(f"🎬 [{v_idx + 1}/{len(unwatched)}] 准备播放: {task['title']}")

                    duration = client.get_video_duration(task["source_url"])
                    if duration <= 1:
                        self.log(
                            f"⚠️ 无法从视频源探测到有效时长 (url: {task['source_url'][:40]}...)，默认使用 600 秒...",
                            "WARN",
                        )
                        duration = 600.0

                    with self.lock:
                        self.state["current_duration"] = duration

                    pos = task["video_pos"]
                    self.log(f"⏱️ 视频时长: {duration:.1f}s, 起始位置: {pos:.1f}s")

                    ok_init, resp_init = client.mark_video(
                        cid=task["cid"],
                        chapter_id=task["chapter_id"],
                        section_id=task["section_id"],
                        subsection_id=task["subsection_id"],
                        resource_id=task["resource_id"],
                        video_length=duration,
                        video_pos=pos,
                    )
                    if ok_init and resp_init == 1:
                        self.log(f"✅ 视频已处于完成状态: {task['title']}", "SUCCESS")
                        with self.lock:
                            self.state["stats"]["completed_videos"] += 1
                        continue

                    cur_speed = float(speed)
                    cur_pos = pos
                    t_last_tick = time.time()

                    while cur_pos < duration:
                        if self.stop_requested:
                            break

                        if not self._sleep_check(interval):
                            break

                        elapsed_time = time.time() - t_last_tick
                        advance = elapsed_time * cur_speed
                        next_pos = min(duration, cur_pos + advance)

                        if duration - next_pos <= 2.0:
                            next_pos = duration

                        pct = min(100.0, (next_pos / duration) * 100.0)
                        with self.lock:
                            self.state["current_pos"] = next_pos
                            self.state["progress_percent"] = pct

                        ok_mark, resp = client.mark_video(
                            cid=task["cid"],
                            chapter_id=task["chapter_id"],
                            section_id=task["section_id"],
                            subsection_id=task["subsection_id"],
                            resource_id=task["resource_id"],
                            video_length=duration,
                            video_pos=next_pos,
                        )

                        if not ok_mark:
                            if resp == "DRAG_NOT_ALLOWED":
                                self.log("⚠️ 触发服务端防拖拽校验 (code=600)，平滑重置基准并降速重试...", "WARN")
                                cur_speed = 1.25 if cur_speed > 1.25 else 1.0
                                t_last_tick = time.time()
                                continue
                            if str(resp).startswith("GATE_LOCKED"):
                                self.log(f"🔒 遇到前置测验限制，跳过当前视频: {resp}", "WARN")
                                with self.lock:
                                    self.state["stats"]["locked_videos"] += 1
                                break
                            self.log(f"⚠️ 上报心跳失败: {resp}，下次间隔重试", "WARN")
                            t_last_tick = time.time()
                            continue

                        is_fin = resp == 1
                        cur_pos = next_pos
                        t_last_tick = time.time()
                        self.log(
                            f"▶️ 进度上报: {cur_pos:.1f}s / {duration:.1f}s ({pct:.1f}%) [倍率: {cur_speed:.2f}x] | 完成: {is_fin}"
                        )

                        if is_fin or cur_pos >= duration:
                            if not is_fin:
                                client.mark_video(
                                    cid=task["cid"],
                                    chapter_id=task["chapter_id"],
                                    section_id=task["section_id"],
                                    subsection_id=task["subsection_id"],
                                    resource_id=task["resource_id"],
                                    video_length=duration,
                                    video_pos=duration,
                                )
                            self.log(f"✅ 视频学习完成: {task['title']}", "SUCCESS")
                            with self.lock:
                                self.state["stats"]["completed_videos"] += 1
                            break

                progress = client.get_course_progress(cid)
                self.log(f"🏁 课程 {c_name} 处理完毕，最新视频进度: {progress.get('video_progress', '未知')}")

            if self.stop_requested:
                self.log("⏹ 任务已安全停止", "WARN")
                with self.lock:
                    self.state["status"] = "stopped"
                    self.state["action"] = "任务已停止"
            else:
                self.log("🎊 所有选定课程处理流程已全部结束！", "SUCCESS")
                with self.lock:
                    self.state["status"] = "idle"
                    self.state["action"] = "所有选定课程已处理完成！"
                    self.state["progress_percent"] = 100.0
        except Exception as e:
            self.log(f"❌ 后台线程异常: {e}", "ERROR")
            with self.lock:
                self.state["status"] = "error"
                self.state["action"] = f"执行异常: {e}"
        finally:
            with self.lock:
                self.state["running"] = False
                self.state["paused"] = False
