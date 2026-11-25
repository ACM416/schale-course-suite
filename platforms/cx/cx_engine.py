# -*- coding: utf-8 -*-
import base64
import hashlib
import json
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
try:
    from Crypto.Cipher import AES
except ImportError:
    from Cryptodome.Cipher import AES
from requests.adapters import HTTPAdapter
from requests.cookies import RequestsCookieJar, create_cookie

AES_KEY = "u2oh6Vu^HWe4_AES"
ENC_SALT = "d_yHJ!$pdA~5"

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

VIDEO_HEADERS = {
    "Referer": "https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2025-0725-1842",
}

AUDIO_HEADERS = {
    "Referer": "https://mooc1.chaoxing.com/ananas/modules/audio/index_new.html?v=2025-0725-1842",
}


def get_timestamp() -> str:
    return str(int(time.time() * 1000))


def aes_encrypt(plaintext: str) -> str:
    raw = plaintext.encode("utf-8")
    pad_len = 16 - (len(raw) % 16)
    padded = raw + bytes([pad_len] * pad_len)
    key = AES_KEY.encode("utf-8")
    cipher = AES.new(key, AES.MODE_CBC, iv=key)
    encrypted = cipher.encrypt(padded)
    return base64.b64encode(encrypted).decode("utf-8")


def cookie_jar_to_list(cookie_jar: RequestsCookieJar) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for c in cookie_jar:
        items.append(
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain or ".chaoxing.com",
                "path": c.path or "/",
                "secure": bool(c.secure),
                "expires": c.expires,
            }
        )
    return items


def list_to_cookie_jar(data: Any) -> RequestsCookieJar:
    jar = RequestsCookieJar()
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            c = create_cookie(
                name=str(item["name"]),
                value=str(item.get("value", "")),
                domain=item.get("domain") or ".chaoxing.com",
                path=item.get("path") or "/",
                secure=bool(item.get("secure", False)),
                expires=item.get("expires"),
            )
            jar.set_cookie(c)
    elif isinstance(data, dict):
        for k, v in data.items():
            jar.set(str(k), str(v), domain=".chaoxing.com", path="/")
    return jar


def parse_cookie_input(raw: str) -> RequestsCookieJar:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Cookie 不能为空")
    if text.startswith("[") or text.startswith("{"):
        return list_to_cookie_jar(json.loads(text))
    jar = RequestsCookieJar()
    for part in text.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            k = k.strip()
            if k:
                jar.set(k, v.strip(), domain=".chaoxing.com", path="/")
    if len(jar) == 0:
        raise ValueError("未解析到有效 Cookie 键值对")
    return jar


class ChaoxingClient:
    def __init__(self):
        self._lock = threading.Lock()
        self.session = self._new_session()
        self._qr_sessions: Dict[str, Tuple[float, requests.Session]] = {}

    @staticmethod
    def _new_session() -> requests.Session:
        s = requests.Session()
        adapter = HTTPAdapter(max_retries=3)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers.update(BASE_HEADERS)
        return s

    def load_cookies(self, data: Any) -> None:
        with self._lock:
            self.session = self._new_session()
            self.session.cookies.update(list_to_cookie_jar(data))

    def set_cookie_string(self, raw: str) -> None:
        jar = parse_cookie_input(raw)
        with self._lock:
            self.session = self._new_session()
            self.session.cookies.update(jar)

    def clear_cookies(self) -> None:
        with self._lock:
            self.session = self._new_session()

    def export_cookies(self) -> List[Dict[str, Any]]:
        with self._lock:
            return cookie_jar_to_list(self.session.cookies)

    def _prune_qr_sessions(self) -> None:
        now = time.time()
        expired = [k for k, (ts, _) in self._qr_sessions.items() if now - ts > 600]
        for k in expired:
            self._qr_sessions.pop(k, None)

    def create_qr(self) -> Dict[str, Any]:
        self._prune_qr_sessions()
        qr_sess = self._new_session()
        login_url = "https://passport2.chaoxing.com/login?fid=&newversion=true&refer=https%3A%2F%2Fi.chaoxing.com"
        resp = qr_sess.get(login_url, timeout=12)
        resp.raise_for_status()

        uuid_m = re.search(r'value="([^"]+)"\s+id="uuid"|id="uuid"\s+value="([^"]+)"', resp.text)
        enc_m = re.search(r'value="([^"]+)"\s+id="enc"|id="enc"\s+value="([^"]+)"', resp.text)
        if not uuid_m or not enc_m:
            raise RuntimeError("未能从学习通登录页提取二维码 uuid/enc")

        uuid_val = uuid_m.group(1) or uuid_m.group(2)
        enc_val = enc_m.group(1) or enc_m.group(2)

        img_resp = qr_sess.get(
            "https://passport2.chaoxing.com/createqr",
            params={"uuid": uuid_val, "fid": "-1"},
            headers={"Referer": login_url},
            timeout=12,
        )
        img_resp.raise_for_status()
        img_b64 = base64.b64encode(img_resp.content).decode("utf-8")

        self._qr_sessions[uuid_val] = (time.time(), qr_sess)
        return {
            "uuid": uuid_val,
            "enc": enc_val,
            "img": f"data:image/png;base64,{img_b64}",
        }

    def poll_qr(self, uuid_val: str, enc_val: str) -> Dict[str, Any]:
        entry = self._qr_sessions.get(uuid_val)
        qr_sess = entry[1] if entry else self._new_session()
        resp = qr_sess.post(
            "https://passport2.chaoxing.com/getauthstatus/v2",
            data={
                "enc": enc_val,
                "uuid": uuid_val,
                "doubleFactorLogin": "0",
                "forbidotherlogin": "0",
            },
            headers={
                "Referer": "https://passport2.chaoxing.com/login?fid=&newversion=true&refer=https%3A%2F%2Fi.chaoxing.com",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") is True:
            # Visit space index to ensure full domain cookies are settled
            try:
                qr_sess.get("https://i.chaoxing.com/base", timeout=8)
            except Exception:
                pass
            cookies_list = cookie_jar_to_list(qr_sess.cookies)
            uid_val = str(qr_sess.cookies.get("_uid") or qr_sess.cookies.get("UID") or "")
            name_val = ""
            try:
                resp_name = qr_sess.get("https://passport2.chaoxing.com/mooc/accountManage", timeout=10)
                if resp_name.status_code == 200:
                    m_name = re.search(r'id="messageName"\s+value="([^"]*)"', resp_name.text)
                    if m_name:
                        name_val = m_name.group(1).strip()
            except Exception:
                pass
            with self._lock:
                self.session = qr_sess
            self._qr_sessions.pop(uuid_val, None)
            return {
                "status": "success",
                "msg": "扫码登录成功！",
                "uid": uid_val,
                "name": name_val,
                "cookies": cookies_list,
            }

        qtype = str(data.get("type", ""))
        if qtype == "4":
            nick = data.get("nickname") or ""
            return {"status": "scanned", "msg": f"已扫码 ({nick})，请在学习通 APP 上点击确认登录"}
        if qtype == "2":
            self._qr_sessions.pop(uuid_val, None)
            return {"status": "expired", "msg": "二维码已过期，请重新生成"}
        if qtype == "6":
            self._qr_sessions.pop(uuid_val, None)
            return {"status": "canceled", "msg": "手机端已取消登录"}
        if qtype == "7":
            self._qr_sessions.pop(uuid_val, None)
            return {"status": "expired", "msg": data.get("mes") or "登录验证未通过，请刷新二维码"}
        return {"status": "waiting", "msg": "等待手机学习通 APP 扫码..."}

    def login_password(self, username: str, password: str) -> Tuple[bool, str]:
        sess = self._new_session()
        resp = sess.post(
            "https://passport2.chaoxing.com/fanyalogin",
            data={
                "fid": "-1",
                "uname": aes_encrypt(username.strip()),
                "password": aes_encrypt(password),
                "refer": "https%3A%2F%2Fi.chaoxing.com",
                "t": True,
                "forbidotherlogin": 0,
                "validate": "",
                "doubleFactorLogin": 0,
                "independentId": 0,
            },
            timeout=12,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") is True:
            with self._lock:
                self.session = sess
            return True, "账号密码登录成功"
        return False, str(data.get("msg2") or data.get("msg") or "账号或密码错误")

    def get_uid(self) -> str:
        c = self.session.cookies
        return str(c.get("_uid") or c.get("UID") or "")

    def get_fid(self) -> str:
        return str(self.session.cookies.get("fid") or "1024")

    def validate_session(self) -> bool:
        if not self.get_uid():
            return False
        try:
            resp = self.session.post(
                "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata",
                data={"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0},
                headers={
                    "Referer": "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction"
                },
                timeout=10,
            )
            if resp.status_code != 200:
                return False
            if "passport2.chaoxing.com" in resp.url or "passport2.chaoxing.com" in resp.text[:1200]:
                return False
            return True
        except Exception:
            return False

    def get_name(self) -> str:
        try:
            resp = self.session.get("https://passport2.chaoxing.com/mooc/accountManage", timeout=10)
            if resp.status_code == 200:
                m = re.search(r'id="messageName"\s+value="([^"]*)"', resp.text)
                if m:
                    return m.group(1).strip()
        except Exception:
            pass
        return ""

    @staticmethod
    def _decode_course_list(html_text: str) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")
        courses: List[Dict[str, Any]] = []
        for div in soup.select("div.course"):
            if div.select_one("a.not-open-tip") or div.select_one("div.not-open-tip"):
                continue
            clazz_el = div.select_one("input.clazzId")
            course_el = div.select_one("input.courseId")
            link_el = div.select_one("a")
            title_el = div.select_one("span.course-name")
            if not (clazz_el and course_el and link_el and title_el):
                continue

            href = link_el.attrs.get("href", "")
            cpi_m = re.search(r"cpi=([^&]+)", href)
            cpi = cpi_m.group(1) if cpi_m else ""
            clazz_id = str(clazz_el.attrs.get("value", ""))
            course_id = str(course_el.attrs.get("value", ""))
            title = str(title_el.attrs.get("title") or title_el.get_text(strip=True))
            teacher_el = div.select_one("p.color3")
            teacher = str(teacher_el.attrs.get("title") or teacher_el.get_text(strip=True)) if teacher_el else ""
            desc_el = div.select_one("p.margint10")
            desc = str(desc_el.attrs.get("title") or desc_el.get_text(strip=True)) if desc_el else ""
            img_el = div.select_one("img")
            cover = str(img_el.attrs.get("src", "")) if img_el else ""

            course_url = (
                f"https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse"
                f"?courseid={course_id}&clazzid={clazz_id}&cpi={cpi}&ut=s"
            )
            discuss_url = (
                f"https://mooc2-ans.chaoxing.com/mooc2-ans/bbscircle/grouptopic"
                f"?courseId={course_id}&clazzid={clazz_id}&cpi={cpi}&ut=s"
            )

            courses.append(
                {
                    "id": f"{course_id}_{clazz_id}",
                    "courseId": course_id,
                    "clazzId": clazz_id,
                    "cpi": cpi,
                    "title": title,
                    "teacher": teacher,
                    "desc": desc,
                    "cover": cover,
                    "course_url": course_url,
                    "discuss_url": discuss_url,
                }
            )
        return courses

    def get_course_list(self) -> List[Dict[str, Any]]:
        url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata"
        headers = {
            "Referer": "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction?moocDomain=https://mooc1-1.chaoxing.com/mooc-ans"
        }
        resp = self.session.post(
            url,
            headers=headers,
            data={"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0},
            timeout=15,
        )
        resp.raise_for_status()
        seen = set()
        results: List[Dict[str, Any]] = []

        for c in self._decode_course_list(resp.text):
            if c["id"] not in seen:
                seen.add(c["id"])
                results.append(c)

        # Also check course folders
        try:
            inter_resp = self.session.get(
                "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction", timeout=12
            )
            soup = BeautifulSoup(inter_resp.text, "html.parser")
            for li in soup.select("ul.file-list>li"):
                fid = li.attrs.get("fileid")
                if not fid:
                    continue
                f_resp = self.session.post(
                    url,
                    headers=headers,
                    data={"courseType": 1, "courseFolderId": fid, "query": "", "superstarClass": 0},
                    timeout=12,
                )
                for c in self._decode_course_list(f_resp.text):
                    if c["id"] not in seen:
                        seen.add(c["id"])
                        results.append(c)
        except Exception:
            pass

        return results

    def get_course_point(self, course_id: str, clazz_id: str, cpi: str) -> Dict[str, Any]:
        url = (
            f"https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse"
            f"?courseid={course_id}&clazzid={clazz_id}&cpi={cpi}&ut=s"
        )
        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        points: List[Dict[str, Any]] = []
        has_locked = False
        for unit in soup.find_all("div", class_="chapter_unit"):
            for li in unit.find_all("li"):
                div = li.find("div", recursive=False)
                if not div or "id" not in div.attrs:
                    continue
                m = re.match(r"^cur(\d{1,20})$", str(div.attrs["id"]))
                if not m:
                    continue
                point_id = m.group(1)
                a_el = div.select_one("a.clicktitle")
                title = a_el.get_text(" ", strip=True) if a_el else f"章节 {point_id}"

                job_count = 1
                need_unlock = False
                job_inp = div.select_one("input.knowledgeJobCount")
                tips = div.select_one("span.bntHoverTips")
                tips_txt = tips.get_text(strip=True) if tips else ""
                if job_inp and job_inp.attrs.get("value"):
                    try:
                        job_count = int(job_inp.attrs["value"])
                    except ValueError:
                        job_count = 1
                elif "解锁" in tips_txt:
                    need_unlock = True
                    has_locked = True

                is_finished = "已完成" in tips_txt
                points.append(
                    {
                        "id": point_id,
                        "title": title,
                        "jobCount": job_count,
                        "has_finished": is_finished,
                        "need_unlock": need_unlock,
                    }
                )
        return {"hasLocked": has_locked, "points": points}

    @staticmethod
    def _decode_course_card(html_text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if "章节未开放" in html_text:
            return [], {"notOpen": True}
        m = re.search(r"mArg\s*=\s*(\{.*?\});", html_text, re.S)
        if not m:
            return [], {}
        try:
            cards_data = json.loads(m.group(1))
        except Exception:
            return [], {}
        if not isinstance(cards_data, dict):
            return [], {}

        defaults = cards_data.get("defaults") or {}
        job_info = {
            "ktoken": defaults.get("ktoken", ""),
            "mtEnc": defaults.get("mtEnc", ""),
            "reportTimeInterval": defaults.get("reportTimeInterval", 60),
            "defenc": defaults.get("defenc", ""),
            "cardid": defaults.get("cardid", ""),
            "cpi": defaults.get("cpi", ""),
            "knowledgeid": defaults.get("knowledgeid", ""),
        }

        jobs: List[Dict[str, Any]] = []
        for card in cards_data.get("attachments") or []:
            if card.get("isPassed", False):
                continue

            other_info = str(card.get("otherInfo") or "").split("&")[0]
            prop = card.get("property") or {}
            ctype = str(card.get("type") or "").lower()

            if card.get("job") is None:
                if ctype == "read" and not prop.get("read", False):
                    jobs.append(
                        {
                            "type": "read",
                            "name": prop.get("title") or "阅读任务",
                            "jobid": card.get("jobid", ""),
                            "jtoken": card.get("jtoken", ""),
                            "otherinfo": other_info,
                        }
                    )
                continue

            if ctype in ("video", "audio"):
                if not card.get("objectId"):
                    continue
                jobs.append(
                    {
                        "type": ctype,
                        "jobid": card.get("jobid", ""),
                        "name": prop.get("name") or ("音频任务" if ctype == "audio" else "视频任务"),
                        "otherinfo": other_info,
                        "mid": card.get("mid", ""),
                        "objectid": card.get("objectId", ""),
                        "aid": card.get("aid", ""),
                        "playTime": int(card.get("playTime") or 0),
                        "rt": str(prop.get("rt") or ""),
                        "attDuration": card.get("attDuration", ""),
                        "attDurationEnc": card.get("attDurationEnc", ""),
                        "videoFaceCaptureEnc": card.get("videoFaceCaptureEnc", ""),
                    }
                )
            elif ctype == "document":
                jobs.append(
                    {
                        "type": "document",
                        "name": prop.get("name") or "文档阅读",
                        "jobid": card.get("jobid", ""),
                        "otherinfo": other_info,
                        "jtoken": card.get("jtoken", ""),
                        "objectid": prop.get("objectid", ""),
                    }
                )
            elif ctype == "workid":
                jobs.append(
                    {
                        "type": "workid",
                        "name": prop.get("title") or "章节测验/作业",
                        "jobid": card.get("jobid", ""),
                    }
                )
        return jobs, job_info

    def get_job_list(self, course: Dict[str, Any], point: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        job_list: List[Dict[str, Any]] = []
        job_info: Dict[str, Any] = {}
        seen_jobs = set()

        for num in ("0", "1", "2", "3", "4", "5", "6"):
            time.sleep(0.35)
            resp = self.session.get(
                "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards",
                params={
                    "clazzid": course["clazzId"],
                    "courseid": course["courseId"],
                    "knowledgeid": point["id"],
                    "num": num,
                    "ut": "s",
                    "cpi": course["cpi"],
                    "v": "2025-0424-1038-3",
                    "mooc2": 1,
                },
                timeout=12,
            )
            if resp.status_code != 200:
                break
            cards, info = self._decode_course_card(resp.text)
            if info.get("notOpen"):
                return [], info
            if info:
                job_info.update(info)
            for j in cards:
                sig = f"{j.get('type')}:{j.get('jobid')}:{j.get('objectid', '')}"
                if sig not in seen_jobs:
                    seen_jobs.add(sig)
                    job_list.append(j)
            if not cards and not info and int(num) >= 2:
                break

        return job_list, job_info

    @staticmethod
    def get_enc(clazz_id: str, job_id: str, object_id: str, playing_time: int, duration: int, user_id: str) -> str:
        raw = (
            f"[{clazz_id}][{user_id}][{job_id}][{object_id}]"
            f"[{playing_time * 1000}][{ENC_SALT}][{duration * 1000}][0_{duration}]"
        )
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _video_progress_log(
        self,
        course: Dict[str, Any],
        job: Dict[str, Any],
        dtoken: str,
        duration: int,
        playing_time: int,
        dtype: str,
        headers: Dict[str, str],
        isdrag: int = 0,
    ) -> Tuple[bool, str]:
        uid = self.get_uid()
        enc = self.get_enc(
            course["clazzId"],
            job["jobid"],
            job["objectid"],
            playing_time,
            duration,
            uid,
        )
        params: Dict[str, Any] = {
            "clazzId": course["clazzId"],
            "playingTime": playing_time,
            "duration": duration,
            "clipTime": f"0_{duration}",
            "objectId": job["objectid"],
            "otherInfo": job["otherinfo"],
            "courseId": course["courseId"],
            "jobid": job["jobid"],
            "userid": uid,
            "isdrag": isdrag,
            "view": "pc",
            "enc": enc,
            "dtype": dtype,
        }
        if job.get("videoFaceCaptureEnc"):
            params["videoFaceCaptureEnc"] = job["videoFaceCaptureEnc"]
        if job.get("attDuration"):
            params["attDuration"] = job["attDuration"]
        if job.get("attDurationEnc"):
            params["attDurationEnc"] = job["attDurationEnc"]

        url = f"https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/{course['cpi']}/{dtoken}"

        rt = job.get("rt") or ""
        if not rt:
            m = re.search(r"-rt_([1d])", str(job.get("otherinfo", "")))
            if m:
                rt = "0.9" if m.group(1) == "d" else "1"
                job["rt"] = rt

        rt_candidates = [rt] if rt else ["0.9", "1"]
        last_status = "error"
        for rt_val in rt_candidates:
            params["rt"] = rt_val
            params["_t"] = get_timestamp()
            resp = self.session.get(url, params=params, headers=headers, timeout=12)
            text_head = resp.text[:300] if resp.text else ""
            if "验证码" in text_head or "validate" in text_head.lower():
                return False, "captcha"
            if resp.status_code == 200:
                try:
                    j = resp.json()
                    job["rt"] = rt_val
                    return bool(j.get("isPassed")), "ok"
                except Exception:
                    return False, "error"
            if resp.status_code == 403:
                last_status = "403"
                continue
            return False, f"http_{resp.status_code}"

        return False, last_status

    def study_video(
        self,
        course: Dict[str, Any],
        job: Dict[str, Any],
        job_info: Dict[str, Any],
        speed: float = 1.5,
        progress_cb: Optional[Callable[[float, float, str], None]] = None,
        pause_event: Optional[threading.Event] = None,
        stop_event: Optional[threading.Event] = None,
        is_audio: bool = False,
    ) -> str:
        dtype = "Audio" if is_audio else "Video"
        headers = AUDIO_HEADERS if is_audio else VIDEO_HEADERS
        speed = max(1.0, min(float(speed or 1.5), 2.0))

        info_url = f"https://mooc1.chaoxing.com/ananas/status/{job['objectid']}?k={self.get_fid()}&flag=normal"
        meta = self.session.get(info_url, headers=headers, timeout=12).json()
        if meta.get("status") != "success":
            return "error"

        dtoken = str(meta["dtoken"])
        duration = max(1, int(meta["duration"]))
        play_time = min(duration, max(0, int(job.get("playTime", 0)) // 1000))

        def controlled_sleep(sec: float) -> None:
            deadline = time.time() + sec
            while time.time() < deadline:
                if stop_event and stop_event.is_set():
                    raise InterruptedError("用户停止任务")
                while pause_event and pause_event.is_set():
                    if stop_event and stop_event.is_set():
                        raise InterruptedError("用户停止任务")
                    time.sleep(0.25)
                time.sleep(min(0.25, max(0.01, deadline - time.time())))

        # Report initial heartbeat (start from current position)
        passed, status = self._video_progress_log(
            course, job, dtoken, duration, int(play_time), dtype, headers, isdrag=3
        )
        if status == "captcha":
            return "captcha"
        if passed:
            if progress_cb:
                progress_cb(duration, duration, "已完成")
            return "success"

        report_interval = max(30, min(int(job_info.get("reportTimeInterval") or 60), 60))
        since_last_report = 0.0
        forbidden_retries = 0

        while not passed:
            controlled_sleep(1.0)
            play_time = min(float(duration), play_time + speed)
            since_last_report += speed

            if progress_cb:
                progress_cb(play_time, duration, "学习播放中")

            reached_end = play_time >= duration
            if since_last_report >= report_interval or reached_end:
                isdrag = 4 if reached_end else 0
                passed, status = self._video_progress_log(
                    course, job, dtoken, duration, int(play_time), dtype, headers, isdrag=isdrag
                )
                since_last_report = 0.0

                if status == "captcha":
                    return "captcha"
                if status == "403":
                    forbidden_retries += 1
                    if forbidden_retries > 2:
                        return "forbidden"
                    # Refresh dtoken metadata once
                    try:
                        refreshed = self.session.get(info_url, headers=headers, timeout=12).json()
                        if refreshed.get("status") == "success" and refreshed.get("dtoken"):
                            dtoken = str(refreshed["dtoken"])
                            continue
                    except Exception:
                        pass
                    return "forbidden"
                if status != "ok":
                    return "error"

                if reached_end and not passed:
                    # One final confirmation ping after short pause
                    controlled_sleep(2.0)
                    passed, status = self._video_progress_log(
                        course, job, dtoken, duration, duration, dtype, headers, isdrag=4
                    )
                    if status == "captcha":
                        return "captcha"
                    break

        if progress_cb:
            progress_cb(duration, duration, "已完成")
        return "success"

    def study_document(self, course: Dict[str, Any], job: Dict[str, Any]) -> str:
        m = re.search(r"nodeId_([^-]+)-", str(job.get("otherinfo", "")))
        knowledge_id = m.group(1) if m else ""
        url = (
            f"https://mooc1.chaoxing.com/ananas/job/document"
            f"?jobid={job['jobid']}&knowledgeid={knowledge_id}"
            f"&courseid={course['courseId']}&clazzid={course['clazzId']}"
            f"&jtoken={job['jtoken']}&_dc={get_timestamp()}"
        )
        resp = self.session.get(url, timeout=12)
        return "success" if resp.status_code == 200 else "error"

    def study_read(self, course: Dict[str, Any], job: Dict[str, Any], job_info: Dict[str, Any]) -> str:
        resp = self.session.get(
            "https://mooc1.chaoxing.com/ananas/job/readv2",
            params={
                "jobid": job["jobid"],
                "knowledgeid": job_info.get("knowledgeid", ""),
                "jtoken": job["jtoken"],
                "courseid": course["courseId"],
                "clazzid": course["clazzId"],
            },
            timeout=12,
        )
        return "success" if resp.status_code == 200 else "error"
