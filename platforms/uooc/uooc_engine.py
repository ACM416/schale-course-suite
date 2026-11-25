# -*- coding: utf-8 -*-
import logging
import re
import struct
import subprocess
import requests

logger = logging.getLogger("uooc_engine")

BASE_URL = "https://www.uooc.net.cn"


def get_headers(cookie: str):
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": f"{BASE_URL}/home",
        "Cookie": cookie.strip(),
        "Accept": "application/json, text/plain, */*",
        "XSRF": "",
    }


def _parse_mp4_mvhd_duration(buf: bytes) -> float:
    """Pure-Python MP4 mvhd duration parser (no ffprobe required on Windows/Linux)."""
    idx = buf.find(b"mvhd")
    if idx == -1 or idx + 24 > len(buf):
        return 0.0
    version = buf[idx + 4]
    try:
        if version == 0 and idx + 24 <= len(buf):
            timescale, duration = struct.unpack(">II", buf[idx + 16 : idx + 24])
        elif version == 1 and idx + 36 <= len(buf):
            timescale, duration = struct.unpack(">IQ", buf[idx + 24 : idx + 36])
        else:
            return 0.0
        if timescale > 0 and duration > 0:
            return float(duration) / float(timescale)
    except Exception:
        pass
    return 0.0


class UoocClient:
    def __init__(self, cookie: str):
        self.cookie = cookie.strip()
        self.session = requests.Session()
        self.session.headers.update(get_headers(self.cookie))

    def get_user(self):
        url = f"{BASE_URL}/home/member/user"
        try:
            r = self.session.get(url, timeout=10)
            data = r.json()
            if data.get("code") == 1 and data.get("data"):
                return True, data["data"]
            return False, data.get("msg", "未登录或Cookie失效")
        except Exception as e:
            return False, str(e)

    def get_courses(self):
        courses = []
        page = 1
        while True:
            url = f"{BASE_URL}/home/course/list?page={page}&type=learn"
            try:
                r = self.session.get(url, timeout=12)
                data = r.json()
                if data.get("code") != 1:
                    if page == 1:
                        return False, data.get("msg", "获取课程列表失败")
                    break

                data_obj = data.get("data", {})
                items = data_obj.get("data", [])
                if not items:
                    break

                for c in items:
                    cid = c.get("id")
                    parent_name = (c.get("parent_name") or "").strip()
                    term_name = (c.get("name") or "").strip()
                    full_name = (
                        f"{parent_name} ({term_name})"
                        if (parent_name and term_name)
                        else (parent_name or term_name or "未命名课程")
                    )

                    cover = c.get("cover_img", "")
                    school = c.get("org_name", "")
                    teacher = c.get("uname", "")
                    if c.get("teacher_title"):
                        teacher += f" ({c.get('teacher_title')})"

                    video_prog = "0%"
                    try:
                        prog_data = self.get_course_progress(cid)
                        video_prog = prog_data.get("video_progress", "0%")
                    except Exception:
                        pass

                    courses.append(
                        {
                            "cid": cid,
                            "name": full_name,
                            "progress": video_prog,
                            "cover": cover,
                            "school": school,
                            "teacher": teacher,
                            "term": term_name,
                            "weeks": c.get("weeks", ""),
                            "raw": c,
                        }
                    )

                pages = data_obj.get("page", {}).get("pages", 1)
                if page >= pages:
                    break
                page += 1
            except Exception as e:
                if page == 1:
                    return False, str(e)
                break
        return True, courses

    def get_course_progress(self, cid: int):
        url = f"{BASE_URL}/home/course/progress?cid={cid}"
        try:
            r = self.session.get(url, timeout=10)
            data = r.json()
            if data.get("code") == 1:
                return data.get("data", {})
            return {}
        except Exception:
            return {}

    def get_catalog(self, cid: int):
        url = f"{BASE_URL}/home/learn/getCatalogList?cid={cid}&hidemsg_=true"
        try:
            r = self.session.get(url, timeout=10)
            data = r.json()
            if data.get("code") == 1:
                return True, data.get("data", [])
            return False, data.get("msg", "获取目录失败")
        except Exception as e:
            return False, str(e)

    def get_unit_learn(self, cid: int, chapter_id: int, section_id: int, subsection_id: int = 0):
        params = {
            "chapter_id": chapter_id,
            "cid": cid,
            "hidemsg_": "true",
            "section_id": section_id,
            "show": "",
        }
        if subsection_id > 0:
            params["catalog_id"] = subsection_id
            params["subsection_id"] = subsection_id
        else:
            params["catalog_id"] = section_id

        url = f"{BASE_URL}/home/learn/getUnitLearn"
        try:
            r = self.session.get(url, params=params, timeout=15)
            data = r.json()
            if data.get("code") == 1:
                return True, data.get("data", [])
            elif data.get("code") == 600:
                msg = data.get("msg", "")
                if any(k in msg for k in ["测验", "关卡", "前置", "锁"]):
                    return False, f"GATE_LOCKED: {msg}"
                return False, msg or "单元未解锁"
            return False, data.get("msg", "获取单元失败")
        except Exception as e:
            return False, str(e)

    def get_video_duration(self, source_url: str) -> float:
        if not source_url:
            return 0.0

        # 1. Pure-Python HLS (.m3u8) parser
        if ".m3u8" in source_url.lower():
            try:
                r = requests.get(source_url, headers={"User-Agent": self.session.headers["User-Agent"]}, timeout=10)
                if r.status_code == 200:
                    vals = [float(x) for x in re.findall(r"#EXTINF:([\d.]+)", r.text)]
                    if vals:
                        return sum(vals)
            except Exception:
                pass

        # 2. Pure-Python MP4 mvhd probe via Range requests (head + tail 128KB)
        try:
            ua = {"User-Agent": self.session.headers["User-Agent"]}
            r_head = requests.get(source_url, headers={**ua, "Range": "bytes=0-131071"}, timeout=10)
            dur = _parse_mp4_mvhd_duration(r_head.content)
            if dur > 1.0:
                return dur
            r_tail = requests.get(source_url, headers={**ua, "Range": "bytes=-131072"}, timeout=10)
            dur = _parse_mp4_mvhd_duration(r_tail.content)
            if dur > 1.0:
                return dur
        except Exception:
            pass

        # 3. Optional ffprobe fallback (Linux / Windows with ffmpeg installed)
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                source_url,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            out = res.stdout.strip()
            if out:
                return float(out)
        except Exception as e:
            logger.debug(f"ffprobe duration failed: {e}")

        return 0.0

    def mark_video(
        self,
        cid: int,
        chapter_id: int,
        section_id: int,
        subsection_id: int,
        resource_id: int,
        video_length: float,
        video_pos: float,
    ):
        url = f"{BASE_URL}/home/learn/markVideoLearn"
        payload = {
            "chapter_id": str(chapter_id),
            "cid": str(cid),
            "hidemsg_": "true",
            "network": "2",
            "resource_id": str(resource_id),
            "section_id": str(section_id),
            "source": "1",
            "video_length": f"{video_length:.2f}",
            "video_pos": f"{video_pos:.2f}",
        }
        if subsection_id > 0:
            payload["subsection_id"] = str(subsection_id)

        headers = self.session.headers.copy()
        headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
        try:
            r = self.session.post(url, data=payload, headers=headers, timeout=10)
            data = r.json()
            code = data.get("code")
            msg = data.get("msg", "")
            if code == 1:
                return True, data.get("data", {}).get("finished", 0)
            elif code == 600 or "拖拽" in msg:
                return False, "DRAG_NOT_ALLOWED"
            return False, msg or f"上报失败(code={code})"
        except Exception as e:
            return False, str(e)
