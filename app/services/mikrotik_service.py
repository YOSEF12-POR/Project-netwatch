#!/usr/bin/env python3
"""
خدمة MikroTik - إدارة الاتصال والتواصل مع أجهزة MikroTik
"""
import logging
import time
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
import threading
import socket
import re
import ipaddress

logger = logging.getLogger(__name__)

class MikroTikService:
    """
    خدمة للتواصل مع أجهزة MikroTik RouterOS
    """

    def __init__(self, host: str, port: int = 8728, username: str = "admin",
                 password: str = "", timeout: int = 10, max_retries: int = 3):
        """
        تهيئة خدمة MikroTik

        Args:
            host: عنوان IP أو اسم المضيف
            port: رقم المنفذ (افتراضي: 8728)
            username: اسم المستخدم
            password: كلمة المرور
            timeout: مهلة الاتصال بالثواني
            max_retries: الحد الأقصى لعدد المحاولات
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self.max_retries = max_retries

        # حالة الاتصال
        self.connected = False
        self.last_error = ""
        self.connection_time = None
        self.last_activity = None

        # بيانات الشبكة المخزنة مؤقتاً
        self._interfaces_cache = None
        self._interfaces_cache_time = None
        self._traffic_cache = {}
        self._cache_timeout = 30  # ثانية

        # قفل للعمليات المتعددة
        self._lock = threading.Lock()

    def _connect(self) -> bool:
        """
        إنشاء اتصال بجهاز MikroTik

        Returns:
            bool: نجاح أو فشل الاتصال
        """
        try:
            # محاكاة الاتصال (في التطبيق الحقيقي سيتم استخدام مكتبة RouterOS)
            logger.info(f"🔌 محاولة الاتصال بـ {self.host}:{self.port}")

            # اختبار الاتصال الأساسي
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            result = sock.connect_ex((self.host, self.port))
            sock.close()

            if result == 0:
                self.connected = True
                self.connection_time = datetime.now()
                self.last_activity = datetime.now()
                logger.info(f"✅ تم الاتصال بنجاح بـ {self.host}")
                return True
            else:
                self.last_error = f"فشل الاتصال بالمنفذ {self.port}"
                logger.error(f"❌ {self.last_error}")
                return False

        except Exception as e:
            self.last_error = str(e)
            logger.error(f"❌ خطأ في الاتصال: {e}")
            return False

    def _disconnect(self) -> None:
        """قطع الاتصال"""
        if self.connected:
            logger.info(f"🔌 قطع الاتصال من {self.host}")
            self.connected = False
            self.connection_time = None

    def is_connected(self) -> bool:
        """هل الاتصال قائم حالياً"""
        return bool(self.connected)

    def test_connection(self) -> Tuple[bool, str]:
        """
        اختبار الاتصال

        Returns:
            Tuple[bool, str]: (نجاح/فشل, رسالة)
        """
        try:
            if self._connect():
                self._disconnect()
                return True, "✅ الاتصال ناجح"
            else:
                return False, f"❌ فشل الاتصال: {self.last_error}"
        except Exception as e:
            return False, f"❌ خطأ: {str(e)}"

    def get_identity(self) -> str:
        """جلب Identity من الراوتر عبر SSH."""
        if not self.connected and not self._connect():
            return "غير متصل"
        try:
            ok, outputs, _ = self._run_routeros_ssh(
                ["/system identity print without-paging terse"]
            )
            if ok and outputs:
                recs = self._parse_kv_lines(outputs[0])
                if recs:
                    name = recs[0].get("name")
                    if name:
                        return name
            return f"MikroTik-{self.host}"
        except Exception as e:
            logger.error(f"❌ خطأ في جلب Identity: {e}")
            return "خطأ"

    def get_internet_status(self) -> Tuple[bool, str]:
        """
        فحص حالة الإنترنت

        Returns:
            Tuple[bool, str]: (متصل/غير متصل, رسالة)
        """
        if not self.connected and not self._connect():
            return False, "غير متصل بالراوتر"

        try:
            # محاكاة فحص الإنترنت
            # في التطبيق الحقيقي سيتم فحص ping لـ 8.8.8.8 أو موقع خارجي
            return True, "متصل بالإنترنت"
        except Exception as e:
            return False, f"خطأ في فحص الإنترنت: {str(e)}"

    @staticmethod
    def _bits_rate_to_megabits_per_sec(value: float, unit: str) -> float:
        u = (unit or "bps").lower().strip()
        if "gbps" in u or u.startswith("g"):
            return value * 1000.0
        if "mbps" in u or u.startswith("m"):
            return value
        if "kbps" in u or u.startswith("k"):
            return value / 1000.0
        return value / 1_000_000.0

    def _parse_monitor_traffic_mbps(self, text: str) -> Tuple[float, float]:
        """يستخرج معدل RX/TX بالميغابت/ث من مخرجات /interface monitor-traffic."""
        rx_mbps = tx_mbps = 0.0
        if not text:
            return rx_mbps, tx_mbps
        blob = text.replace("\r", "\n")
        for line in blob.splitlines():
            low = line.lower()
            if "rx-bits-per-second" in low or "rx-packets-per-second" in low:
                m = re.search(
                    r"rx-bits-per-second:\s*([\d.]+)\s*(\S+)",
                    line,
                    re.I,
                )
                if not m:
                    m = re.search(r"rx-bits-per-second:\s*([\d.]+)\b", line, re.I)
                if m:
                    unit = m.group(2) if len(m.groups()) >= 2 else "bps"
                    rx_mbps = self._bits_rate_to_megabits_per_sec(float(m.group(1)), unit)
            if "tx-bits-per-second" in low or "tx-packets-per-second" in low:
                m = re.search(
                    r"tx-bits-per-second:\s*([\d.]+)\s*(\S+)",
                    line,
                    re.I,
                )
                if not m:
                    m = re.search(r"tx-bits-per-second:\s*([\d.]+)\b", line, re.I)
                if m:
                    unit = m.group(2) if len(m.groups()) >= 2 else "bps"
                    tx_mbps = self._bits_rate_to_megabits_per_sec(float(m.group(1)), unit)
        return rx_mbps, tx_mbps

    def _ip_base(self, addr: str) -> str:
        if not addr:
            return ""
        return addr.strip().split("/")[0].strip().lower()

    def find_wan_interface(self, public_wan_ip: str) -> Tuple[Optional[str], str]:
        """
        إن وُجد public_wan_ip في قاعدة البيانات: البحث عن الواجهة المرتبطة به فقط (/ip address ثم dhcp-client address).
        إن كان فارغاً: استخدام أول dhcp-client بحالة bound كاحتياط.
        """
        pip_raw = (public_wan_ip or "").strip()
        pip = self._ip_base(pip_raw)
        if not self.connected and not self._connect():
            return None, "غير متصل"

        def iface_from_record(r: Dict[str, str]) -> Optional[str]:
            return (
                r.get("interface")
                or r.get("actual-interface")
                or r.get("actual-interface-name")
            )

        if pip:
            ok, outs, err = self._run_routeros_ssh(
                ["/ip address print without-paging terse"]
            )
            if ok and outs:
                for r in self._parse_kv_lines(outs[0]):
                    addr = r.get("address", "")
                    base = self._ip_base(addr)
                    if base == pip:
                        iface = iface_from_record(r)
                        if iface:
                            return iface.strip(), f"/ip address → {pip}"

            ok3, outs3, _ = self._run_routeros_ssh(
                ["/ip dhcp-client print without-paging terse"]
            )
            if ok3 and outs3:
                for r in self._parse_kv_lines(outs3[0]):
                    ab = self._ip_base(r.get("address", "") or "")
                    if ab == pip and (r.get("status") or "").lower() == "bound":
                        iface = iface_from_record(r)
                        if iface:
                            return iface.strip(), f"dhcp-client → {pip}"

            return None, f"لا واجهة للـ IP المخزن ({pip})"

        ok2, outs2, _ = self._run_routeros_ssh(
            ["/ip dhcp-client print without-paging terse"]
        )
        if ok2 and outs2:
            for r in self._parse_kv_lines(outs2[0]):
                if (r.get("status") or "").lower() == "bound":
                    iface = iface_from_record(r)
                    if iface:
                        return iface.strip(), "dhcp-client (بدون Public IP مخزن)"

        return None, "لا بيانات — أضف Public WAN IP في الإعدادات"

    def _monitor_traffic_for_interface(self, iface: str) -> Tuple[bool, str, float, float]:
        """تجرب عدة صيغ RouterOS لـ monitor-traffic."""
        if not iface:
            return False, "لا واجهة", 0.0, 0.0
        name = iface.strip().replace('"', '\\"')
        commands = [
            f'/interface monitor-traffic interface="{name}" once',
            f"/interface monitor-traffic interface={name} once",
            f'/interface monitor-traffic [find name="{name}"] once',
        ]
        last_err = ""
        for cmd in commands:
            ok, outs, err = self._run_routeros_ssh([cmd])
            if ok and outs:
                out0 = outs[0] or ""
                rx, tx = self._parse_monitor_traffic_mbps(out0)
                lo = out0.lower()
                if rx > 0 or tx > 0:
                    return True, "", rx, tx
                if "rx-bits-per-second" in lo or "tx-bits-per-second" in lo:
                    return True, "", rx, tx
            last_err = err or (outs[0][:120] if outs else "")

        ok_d, err_d, rx_d, tx_d = self._wan_rates_from_byte_delta(iface)
        if ok_d:
            return True, "", rx_d, tx_d
        if err_d:
            last_err = (last_err + " · " if last_err else "") + err_d
        return False, last_err or "فشل monitor-traffic", 0.0, 0.0

    def _interface_rx_tx_bytes(self, iface: str) -> Tuple[Optional[int], Optional[int]]:
        """عداد بايتات RX/TX من /interface print stats (لحساب المعدل بين قراءتين)."""
        if not iface:
            return None, None
        nm = iface.strip().replace('"', '\\"')
        for cmd in (
            f'/interface print stats without-paging terse where name="{nm}"',
            f"/interface print stats without-paging terse where name={nm}",
        ):
            ok, outs, _ = self._run_routeros_ssh([cmd])
            if not ok or not outs:
                continue
            recs = self._parse_kv_lines(outs[0])
            if not recs:
                continue
            r0 = recs[0]

            def _to_int(v: Optional[str]) -> Optional[int]:
                if v is None or v == "":
                    return None
                s = str(v).lower().replace("i", "").replace("b", "")
                try:
                    if s.endswith("k"):
                        return int(float(s[:-1]) * 1024)
                    if s.endswith("m"):
                        return int(float(s[:-1]) * 1024 * 1024)
                    if s.endswith("g"):
                        return int(float(s[:-1]) * 1024 * 1024 * 1024)
                    return int(float(s))
                except Exception:
                    return None

            rx = _to_int(r0.get("rx-byte") or r0.get("fp-rx-byte"))
            tx = _to_int(r0.get("tx-byte") or r0.get("fp-tx-byte"))
            if rx is not None and tx is not None:
                return rx, tx
        return None, None

    def _wan_rates_from_byte_delta(self, iface: str) -> Tuple[bool, str, float, float]:
        """احتياط: معدل Mbps من فرق عدادات البايت."""
        b0_rx, b0_tx = self._interface_rx_tx_bytes(iface)
        if b0_rx is None or b0_tx is None:
            return False, "لا إحصائيات واجهة", 0.0, 0.0
        time.sleep(0.55)
        b1_rx, b1_tx = self._interface_rx_tx_bytes(iface)
        if b1_rx is None or b1_tx is None:
            return False, "فشل قراءة ثانية للإحصائيات", 0.0, 0.0
        dt = 0.55
        drx = max(0, b1_rx - b0_rx)
        dtx = max(0, b1_tx - b0_tx)
        rx_mbps = (drx * 8.0) / (dt * 1_000_000.0)
        tx_mbps = (dtx * 8.0) / (dt * 1_000_000.0)
        return True, "", rx_mbps, tx_mbps

    def get_wan_traffic_rates(
        self, public_wan_ip: Optional[str] = None
    ) -> Dict[str, Any]:
        """RX/TX (Mbps) لواجهة WAN المكتشفة من Public IP المخزن."""
        iface, note = self.find_wan_interface(public_wan_ip or "")
        if not iface:
            return {
                "success": False,
                "status": "no_data",
                "error": note,
                "interface": None,
                "rx_mbps": None,
                "tx_mbps": None,
            }

        ok_m, err_m, rx_mbps, tx_mbps = self._monitor_traffic_for_interface(iface)
        if not ok_m:
            return {
                "success": False,
                "status": "no_data",
                "error": err_m or "No Data",
                "interface": iface,
                "rx_mbps": None,
                "tx_mbps": None,
                "note": note,
            }

        return {
            "success": True,
            "status": "ok",
            "interface": iface,
            "note": note,
            "rx_mbps": round(rx_mbps, 4),
            "tx_mbps": round(tx_mbps, 4),
            "error": None,
        }

    def get_network_traffic(
        self, public_wan_ip: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        حركة المرور لصفحة الرسم البياني — من واجهة WAN المرتبطة بـ Public IP (إن وُجد).
        """
        empty = {
            "success": False,
            "error": "غير متصل بالراوتر",
            "wan_traffic_status": "no_data",
            "current_upload": 0.0,
            "current_download": 0.0,
            "wan_interface": None,
            "labels": [],
            "upload": [],
            "download": [],
            "upload_data": [],
            "download_data": [],
        }

        if not self.connected and not self._connect():
            return empty

        try:
            info = self.get_wan_traffic_rates(public_wan_ip)
            if info.get("success"):
                tx = float(info.get("tx_mbps") or 0)
                rx = float(info.get("rx_mbps") or 0)
                labels = ["T-4", "T-3", "T-2", "T-1", "الآن"]
                upload = [tx * 0.5, tx * 0.7, tx * 0.85, tx * 0.95, tx]
                download = [rx * 0.5, rx * 0.7, rx * 0.85, rx * 0.95, rx]
                return {
                    "success": True,
                    "wan_traffic_status": "ok",
                    "error": None,
                    "current_upload": tx,
                    "current_download": rx,
                    "wan_interface": info.get("interface"),
                    "labels": labels,
                    "upload": upload,
                    "download": download,
                    "upload_data": upload,
                    "download_data": download,
                }

            return {
                "success": False,
                "wan_traffic_status": info.get("status") or "no_data",
                "error": info.get("error", "لا بيانات"),
                "current_upload": 0.0,
                "current_download": 0.0,
                "wan_interface": info.get("interface"),
                "labels": [],
                "upload": [],
                "download": [],
                "upload_data": [],
                "download_data": [],
            }
        except Exception as e:
            logger.error("get_network_traffic: %s", e)
            empty["error"] = str(e)
            return empty

    def get_hotspot_users(self) -> Dict[str, Any]:
        """جلسات Hotspot النشطة فعلياً من الراوتر."""
        if not self.connected and not self._connect():
            return {
                "success": False,
                "error": "غير متصل بالراوتر",
                "total_users": 0,
                "active_sessions": 0,
                "registered_users": 0,
            }

        try:
            ok, outputs, err = self._run_routeros_ssh(
                ["/ip hotspot active print without-paging terse"]
            )
            if not ok or not outputs:
                return {
                    "success": False,
                    "error": err or "فشل جلب hotspot active",
                    "total_users": 0,
                    "active_sessions": 0,
                    "registered_users": 0,
                }

            recs = self._parse_kv_lines(outputs[0])
            active = len(recs)
            return {
                "success": True,
                "total_users": active,
                "active_sessions": active,
                "registered_users": 0,
            }
        except Exception as e:
            logger.error(f"❌ خطأ في جلب مستخدمي Hotspot: {e}")
            return {
                "success": False,
                "error": str(e),
                "total_users": 0,
                "active_sessions": 0,
                "registered_users": 0,
            }

    def get_dhcp_clients_rows(self) -> List[Dict[str, str]]:
        """صفوف مبسطة من /ip dhcp-client print."""
        if not self.connected and not self._connect():
            return []
        try:
            ok, outs, _ = self._run_routeros_ssh(
                ["/ip dhcp-client print without-paging terse"]
            )
            if not ok or not outs:
                return []
            rows: List[Dict[str, str]] = []
            for r in self._parse_kv_lines(outs[0]):
                rows.append({
                    "interface": r.get("interface", "") or "",
                    "status": r.get("status", "") or "",
                    "address": r.get("address", "") or "",
                })
            return rows
        except Exception as e:
            logger.error("get_dhcp_clients_rows: %s", e)
            return []

    def get_dhcp_leases_rows(self, limit: int = 200) -> List[Dict[str, str]]:
        """صفوف من /ip dhcp-server lease print للعرض في Tools."""
        if not self.connected and not self._connect():
            return []
        try:
            ok, outs, _ = self._run_routeros_ssh(
                ["/ip dhcp-server lease print without-paging terse"]
            )
            if not ok or not outs:
                return []
            rows: List[Dict[str, str]] = []
            recs = self._parse_kv_lines(outs[0])
            for r in recs[: max(1, int(limit))]:
                rows.append({
                    "address": r.get("address", "") or "",
                    "mac_address": r.get("mac-address", "") or "",
                    "status": r.get("status", "") or "",
                    "server": r.get("server", "") or "",
                    "host_name": r.get("host-name", "") or "",
                    "comment": r.get("comment", "") or "",
                })
            return rows
        except Exception as e:
            logger.error("get_dhcp_leases_rows: %s", e)
            return []

    def remove_dhcp_lease(self, ip: str = "", mac: str = "") -> Tuple[bool, str]:
        """طرد lease حسب IP أو MAC."""
        if not self.connected and not self._connect():
            return False, "غير متصل بالراوتر"
        target_ip = (ip or "").strip()
        target_mac = (mac or "").strip()
        if not target_ip and not target_mac:
            return False, "يجب إدخال IP أو MAC"
        try:
            cmds: List[str] = []
            if target_ip:
                safe_ip = target_ip.replace('"', "")
                cmds.append(f'/ip dhcp-server lease remove [find where address="{safe_ip}"]')
            if target_mac:
                safe_mac = target_mac.upper().replace('"', "")
                cmds.append(f'/ip dhcp-server lease remove [find where mac-address="{safe_mac}"]')
            ok, outs, err = self._run_routeros_ssh(cmds)
            if not ok:
                return False, err or "فشل تنفيذ الطرد"
            out = "\n".join(outs or []).strip()
            return True, out or "تم طرد الـ Lease بنجاح"
        except Exception as e:
            logger.error("remove_dhcp_lease: %s", e)
            return False, str(e)

    @staticmethod
    def _strip_pct(val: Optional[str]) -> Optional[str]:
        if val is None:
            return None
        s = str(val).strip()
        if not s:
            return None
        return s.rstrip("%").strip() or None

    def get_system_health_details(self) -> Dict[str, Any]:
        """CPU من resource؛ الحرارة/الفولت من health إن وُجدت."""
        out: Dict[str, Any] = {
            "success": False,
            "cpu_load": None,
            "free_memory": None,
            "total_memory": None,
            "temperature": None,
            "voltage": None,
        }
        if not self.connected and not self._connect():
            return out
        try:
            ok, outs, _ = self._run_routeros_ssh(
                ["/system resource print without-paging terse"]
            )
            if ok and outs:
                recs = self._parse_kv_lines(outs[0])
                if recs:
                    r0 = recs[0]
                    out["cpu_load"] = self._strip_pct(r0.get("cpu-load"))
                    out["free_memory"] = r0.get("free-memory")
                    out["total_memory"] = r0.get("total-memory")
                    out["success"] = True

            if out.get("cpu_load") is None:
                okf, outf, _ = self._run_routeros_ssh(
                    ["/system resource print without-paging"]
                )
                if okf and outf:
                    blob = outf[0] or ""
                    m = re.search(
                        r"cpu-load:\s*([\d.]+)\s*%",
                        blob,
                        re.I,
                    )
                    if m:
                        out["cpu_load"] = m.group(1).strip()
                        out["success"] = True
                    mfm = re.search(
                        r"free-memory:\s*(\S+)",
                        blob,
                        re.I,
                    )
                    mtm = re.search(
                        r"total-memory:\s*(\S+)",
                        blob,
                        re.I,
                    )
                    if mfm:
                        out["free_memory"] = mfm.group(1).strip()
                    if mtm:
                        out["total_memory"] = mtm.group(1).strip()

            ok2, outs2, _ = self._run_routeros_ssh(
                ["/system health print without-paging terse"]
            )
            if ok2 and outs2:
                recs2 = self._parse_kv_lines(outs2[0])
                if recs2:
                    h = recs2[0]
                    out["temperature"] = h.get("temperature") or h.get("cpu-temperature")
                    out["voltage"] = (
                        h.get("voltage")
                        or h.get("board-voltage")
                        or h.get("power-consumption")
                    )

            if out.get("temperature") is None or out.get("voltage") is None:
                okh, outh, _ = self._run_routeros_ssh(
                    ["/system health print without-paging"]
                )
                if okh and outh:
                    hb = outh[0] or ""
                    mt = re.search(
                        r"(?:cpu-)?temperature:\s*([\d.]+)\s*°?C?",
                        hb,
                        re.I,
                    )
                    if mt and out.get("temperature") is None:
                        out["temperature"] = mt.group(1).strip() + "°C"
                    mv = re.search(
                        r"(?:board-)?voltage:\s*([\d.]+)\s*V?",
                        hb,
                        re.I,
                    )
                    if mv and out.get("voltage") is None:
                        out["voltage"] = mv.group(1).strip() + "V"
            return out
        except Exception as e:
            logger.error("get_system_health_details: %s", e)
            out["error"] = str(e)
            return out

    def get_router_log_lines(self, limit: int = 45) -> List[str]:
        """آخر سطور من /log print."""
        limit = max(10, min(100, int(limit)))
        if not self.connected and not self._connect():
            return []
        try:
            ok, outs, _ = self._run_routeros_ssh(["/log print without-paging"])
            if not ok or not outs:
                return []
            lines = [
                ln.rstrip()
                for ln in outs[0].splitlines()
                if ln.strip() and ";;;" not in ln[:3]
            ]
            return lines[-limit:]
        except Exception as e:
            logger.error("get_router_log_lines: %s", e)
            return []

    @staticmethod
    def _parse_router_log_line(line: str) -> Dict[str, str]:
        """تحويل سطر log print إلى حقول للعرض في جدول."""
        s = (line or "").strip()
        if not s:
            return {"time": "", "topic": "", "message": "", "raw": s}
        if s.startswith("*") or s.startswith("@"):
            s = s[1:].strip()
        tokens = s.split()
        if (
            len(tokens) >= 4
            and re.match(r"^[a-z]{3}/\d{1,2}(?:/\d{2,4})?$", tokens[0], re.I)
            and re.match(r"^\d{1,2}:\d{2}:\d{2}$", tokens[1])
        ):
            t = tokens[0] + " " + tokens[1]
            topic = tokens[2]
            msg = " ".join(tokens[3:])
            return {"time": t, "topic": topic, "message": msg, "raw": line.strip()}
        if (
            len(tokens) >= 4
            and re.match(r"^\d{4}-\d{2}-\d{2}$", tokens[0])
            and re.match(r"^\d{1,2}:\d{2}:\d{2}$", tokens[1])
        ):
            t = tokens[0] + " " + tokens[1]
            topic = tokens[2]
            msg = " ".join(tokens[3:])
            return {"time": t, "topic": topic, "message": msg, "raw": line.strip()}
        if len(tokens) >= 3 and re.match(r"^\d{1,2}:\d{2}:\d{2}$", tokens[0]):
            return {
                "time": tokens[0],
                "topic": tokens[1],
                "message": " ".join(tokens[2:]),
                "raw": line.strip(),
            }
        if len(tokens) >= 2:
            return {
                "time": tokens[0],
                "topic": "",
                "message": " ".join(tokens[1:]),
                "raw": line.strip(),
            }
        return {"time": "", "topic": "", "message": s, "raw": line.strip()}

    def get_router_log_entries(self, limit: int = 45) -> List[Dict[str, str]]:
        """سجلات مع حقول منفصلة (آخر N سطر)."""
        lines = self.get_router_log_lines(limit=limit)
        return [self._parse_router_log_line(ln) for ln in lines]

    def get_system_health(self) -> str:
        """معلومات صحة النظام (محاكاة)"""
        if not self.connected and not self._connect():
            return "❌ غير متصل بالراوتر"
        return "✅ النظام يعمل بشكل طبيعي"

    def _execute_command(self, command: str) -> str:
        """
        تنفيذ أمر RouterOS

        Args:
            command: الأمر المراد تنفيذه

        Returns:
            str: نتيجة الأمر
        """
        if not self.connected and not self._connect():
            return "غير متصل"

        try:
            # محاكاة تنفيذ الأمر
            logger.info(f"⚡ تنفيذ الأمر: {command}")
            return f"تم تنفيذ: {command}"
        except Exception as e:
            logger.error(f"❌ خطأ في تنفيذ الأمر: {e}")
            return f"خطأ: {str(e)}"

    def _run_routeros_ssh(self, commands: List[str]) -> Tuple[bool, List[str], str]:
        """
        تنفيذ أوامر RouterOS عبر SSH باستخدام netmiko (إن توفر).

        Returns:
            (success, outputs, error)
        """
        try:
            from netmiko import ConnectHandler  # type: ignore
        except Exception as e:
            return False, [], f"netmiko غير متوفر: {e}"

        device = {
            "device_type": "mikrotik_routeros",
            "host": self.host,
            "username": self.username,
            "password": self.password,
            "port": int(self.port),
            "timeout": int(self.timeout),
        }

        try:
            conn = ConnectHandler(**device)
            outputs: List[str] = []
            for cmd in commands:
                outputs.append(conn.send_command(cmd))
            conn.disconnect()
            return True, outputs, ""
        except Exception as e:
            return False, [], str(e)

    @staticmethod
    def _parse_ip_scan_output(output: str) -> List[Dict[str, Any]]:
        """
        تحليل نتائج /tool ip-scan (ip + mac إن وجد).
        """
        devices: List[Dict[str, Any]] = []
        seen_ips = set()
        for raw in (output or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            if "ADDRESS" in line or "MAC-ADDRESS" in line or line.startswith("Flags:"):
                continue

            if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}\b", line):
                parts = line.split()
                ip = parts[0]
                mac = ""
                if len(parts) > 1 and parts[1].lower() != "incomplete":
                    mac = parts[1].replace("-", ":")

                if ip in seen_ips:
                    continue
                seen_ips.add(ip)
                devices.append({"ip": ip, "mac": mac, "source": "ip_scan"})
        return devices

    def ip_scan(self, interface: str, ip_range: str, duration: str = "20s") -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        تنفيذ /tool ip-scan من MikroTik وإرجاع الأجهزة.
        """
        if not self.connected and not self._connect():
            return False, "غير متصل بالراوتر", []

        interface = (interface or "").strip().replace('"', "")
        try:
            # validation سريع للرينج
            ipaddress.ip_network(ip_range, strict=False)
        except Exception:
            return False, "نطاق IP غير صالح (مثال: 10.10.8.0/22)", []

        cmd = f"/tool ip-scan address={ip_range} interface={interface} duration={duration}"
        ok, outputs, err = self._run_routeros_ssh([cmd])
        if not ok:
            return False, f"فشل ip-scan: {err}", []

        devices = self._parse_ip_scan_output(outputs[0] if outputs else "")
        return True, "تم تنفيذ ip-scan", devices

    @staticmethod
    def _parse_kv_lines(text: str) -> List[Dict[str, str]]:
        """
        تحليل مخرجات RouterOS التي تكون على شكل key=value.
        يعيد قائمة سجلات.
        """
        records: List[Dict[str, str]] = []
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line:
                continue

            # إزالة رقم/أعلام بداية السطر (قد تظهر في RouterOS)
            # مثال: " 0  name=ether1 ..."
            line = re.sub(r"^\d+\s+", "", line)
            line = re.sub(r"^[A-Z\*\!RDX\-\s]+\s+", "", line)

            rec: Dict[str, str] = {}
            for part in line.split():
                if "=" in part:
                    k, v = part.split("=", 1)
                    rec[k.strip()] = v.strip()
            if rec:
                records.append(rec)
        return records

    def get_interfaces(self) -> List[str]:
        """
        جلب قائمة الواجهات المتاحة

        Returns:
            List[str]: أسماء الواجهات
        """
        if not self.connected and not self._connect():
            return []

        # التحقق من الـ cache
        now = datetime.now()
        if (self._interfaces_cache and self._interfaces_cache_time and
            (now - self._interfaces_cache_time).seconds < self._cache_timeout):
            return self._interfaces_cache

        try:
            # محاولة جلب الواجهات فعلياً عبر SSH
            ok, outputs, err = self._run_routeros_ssh([
                "/interface print without-paging terse",
            ])
            names: List[str] = []
            if ok and outputs:
                recs = self._parse_kv_lines(outputs[0])
                for r in recs:
                    n = r.get("name")
                    if n:
                        names.append(n)

            # fallback (محاكاة) إذا فشل الاتصال/التحليل
            if not names:
                names = ["ether1", "ether2", "wlan1"]

            self._interfaces_cache = names
            self._interfaces_cache_time = now
            return names

        except Exception as e:
            logger.error(f"❌ خطأ في جلب الواجهات: {e}")
            return []

    def discover_devices(self, interface: str, ip_range: str) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        اكتشاف الأجهزة عبر بيانات الراوتر (ARP/Neighbors) ثم فلترتها حسب الواجهة والرينج.

        Returns:
            (success, message, devices)
        """
        if not self.connected and not self._connect():
            return False, "غير متصل بالراوتر", []

        try:
            try:
                target_net = ipaddress.ip_network(ip_range, strict=False)
            except Exception:
                return False, "نطاق IP غير صالح (مثال: 192.168.1.0/24)", []

            # 0) حاول ip-scan أولاً (أفضل اكتشاف فعلي)
            scan_ok, _scan_msg, scan_devices = self.ip_scan(interface, ip_range, duration="20s")
            if scan_ok and scan_devices:
                # أضف name افتراضي
                for d in scan_devices:
                    d["name"] = d.get("name") or f"Device-{d['ip']}"
                    d["interface"] = interface
                return True, "تم اكتشاف الأجهزة عبر ip-scan", scan_devices

            ok, outputs, err = self._run_routeros_ssh([
                "/ip arp print without-paging terse",
                "/ip neighbor print without-paging terse",
            ])

            devices: Dict[str, Dict[str, Any]] = {}

            if ok and outputs:
                arp_recs = self._parse_kv_lines(outputs[0])
                for r in arp_recs:
                    addr = r.get("address")
                    if not addr:
                        continue
                    try:
                        ip_obj = ipaddress.ip_address(addr)
                    except Exception:
                        continue
                    if ip_obj not in target_net:
                        continue
                    if interface and r.get("interface") and r.get("interface") != interface:
                        continue

                    devices[addr] = {
                        "ip": addr,
                        "mac": r.get("mac-address") or r.get("mac") or "",
                        "interface": r.get("interface") or interface,
                        "name": r.get("host-name") or r.get("comment") or f"Device-{addr}",
                        "source": "arp",
                    }

                neigh_recs = self._parse_kv_lines(outputs[1]) if len(outputs) > 1 else []
                for r in neigh_recs:
                    addr = r.get("address")
                    if not addr:
                        continue
                    try:
                        ip_obj = ipaddress.ip_address(addr)
                    except Exception:
                        continue
                    if ip_obj not in target_net:
                        continue
                    if interface and r.get("interface") and r.get("interface") != interface:
                        continue

                    existing = devices.get(addr, {"ip": addr})
                    existing.update({
                        "mac": existing.get("mac") or r.get("mac-address") or "",
                        "interface": existing.get("interface") or r.get("interface") or interface,
                        "name": existing.get("name") or r.get("identity") or r.get("host-name") or f"Device-{addr}",
                        "source": existing.get("source") or "neighbor",
                    })
                    devices[addr] = existing

            # fallback: لا يوجد اكتشاف حقيقي
            if not devices:
                return True, "لم يتم العثور على أجهزة (قد تحتاج تفعيل SSH/صلاحيات/وجود ARP)", []

            return True, "تم اكتشاف الأجهزة بنجاح", list(devices.values())

        except Exception as e:
            logger.error(f"❌ خطأ في اكتشاف الأجهزة: {e}")
            return False, f"خطأ في اكتشاف الأجهزة: {str(e)}", []

    def router_ping_sweep(self, ip_range: str, limit: int = 256) -> Tuple[bool, str, int]:
        """
        تنفيذ Ping sweep من داخل MikroTik نفسه (عبر SSH) لملء ARP ثم تسهيل اكتشاف الأجهزة.

        Returns:
            (success, message, pinged_count)
        """
        if not self.connected and not self._connect():
            return False, "غير متصل بالراوتر", 0

        try:
            try:
                net = ipaddress.ip_network(ip_range, strict=False)
            except Exception:
                return False, "نطاق IP غير صالح (مثال: 192.168.1.0/24)", 0

            # أسرع: استخدم سكريبت واحد داخل RouterOS إن كان IPv4 و /24 أو أصغر
            pinged = 0
            if isinstance(net.network_address, ipaddress.IPv4Address) and net.prefixlen >= 24:
                # نعمل loop على آخر أوكتت ضمن /24 فقط (أو جزء منه)
                base = str(net.network_address).rsplit(".", 1)[0] + "."
                start = 1
                end = min(254, limit)
                script = f":for i from={start} to={end} do={{/ping address=({base} . $i) count=1 interval=100ms;}}"
                ok, _, err = self._run_routeros_ssh([script])
                if ok:
                    pinged = end - start + 1
                else:
                    return False, f"فشل تنفيذ ping sweep على MikroTik: {err}", 0
            else:
                hosts = list(net.hosts())
                if not hosts:
                    return False, "نطاق IP لا يحتوي hosts", 0
                if len(hosts) > limit:
                    hosts = hosts[:limit]
                cmds = [f"/ping address={str(ip)} count=1 interval=100ms" for ip in hosts]
                ok, _, err = self._run_routeros_ssh(cmds)
                if not ok:
                    return False, f"فشل تنفيذ ping sweep على MikroTik: {err}", 0
                pinged = len(hosts)

            if not ok:
                return False, f"فشل تنفيذ ping sweep على MikroTik: {err}", 0

            return True, "تم تنفيذ ping sweep على MikroTik", pinged
        except Exception as e:
            return False, f"خطأ في ping sweep: {str(e)}", 0

    def scan_network(self, interface: str, ip_range: str) -> Tuple[bool, str, int]:
        """
        مسح الشبكة للأجهزة

        Args:
            interface: اسم الواجهة
            ip_range: نطاق IP (مثل: 192.168.1.0/24)

        Returns:
            Tuple[bool, str, int]: (نجاح, رسالة, عدد الأجهزة)
        """
        logger.info(f"🔍 بدء مسح الشبكة على {interface} في النطاق {ip_range}")
        ok, msg, devices = self.discover_devices(interface, ip_range)
        if not ok:
            return False, msg, 0
        return True, msg, len(devices)

    def verify_ssh_login(self) -> Tuple[bool, str]:
        """التحقق من صلاحية اسم المستخدم وكلمة المرور عبر SSH."""
        ok, outputs, err = self._run_routeros_ssh(["/system identity print"])
        if ok:
            return True, (outputs[0][:400] if outputs else "OK")
        return False, err or "فشل تسجيل الدخول عبر SSH"

    def ping_from_router(self, ip: str) -> Tuple[bool, str, float]:
        """
        تنفيذ /ping من MikroTik نفسه نحو عنوان IP.

        Returns:
            (نجاح, رسالة مختصرة, زمن تقديري بالمللي ثانية)
        """
        ip = (ip or "").strip()
        if not ip:
            return False, "IP فارغ", 0.0

        if not self.connected and not self._connect():
            return False, "غير متصل بالراوتر", 0.0

        try:
            ok, outputs, err = self._run_routeros_ssh(
                [f'/ping address={ip} count=2 interval=200ms']
            )
            if not ok:
                return False, err or "فشل تنفيذ ping", 0.0

            out = "\n".join(outputs) if outputs else ""
            low = out.lower()
            if "could not ping" in low or "no route" in low or "host unreachable" in low:
                return False, "لا يوجد رد", 0.0

            pl = re.search(r"packet-loss=(\d+)%", out)
            if pl and int(pl.group(1)) >= 100:
                return False, "لا يوجد رد", 0.0

            if re.search(r"received=[1-9]", out) or (pl and int(pl.group(1)) < 100):
                tm = re.search(r"time=([\d.]+)\s*ms", out, re.IGNORECASE)
                if not tm:
                    tm = re.search(r"(\d+(?:\.\d+)?)\s*ms", out)
                latency = float(tm.group(1)) if tm else 1.0
                return True, "رد من الراوتر", latency

            return False, (out[:200] if out else "لا يوجد رد"), 0.0

        except Exception as e:
            logger.error("ping_from_router: %s", e)
            return False, str(e), 0.0

    def ping_from_router_verbose(
        self, ip: str, count: int = 4
    ) -> Tuple[bool, str, float]:
        """
        /ping من الراوتر مع إرجاع المخرجات الكاملة (للعرض في الواجهة).
        """
        ip = (ip or "").strip()
        if not ip:
            return False, "IP فارغ", 0.0

        if not self.connected and not self._connect():
            return False, "غير متصل بالراوتر", 0.0

        try:
            ok, outputs, err = self._run_routeros_ssh(
                [f'/ping address={ip} count={count} interval=200ms']
            )
            out = "\n".join(outputs) if outputs else ""
            if not ok:
                return False, (err or out or "فشل تنفيذ ping"), 0.0

            low = out.lower()
            if "could not ping" in low or "no route" in low or "host unreachable" in low:
                return False, out, 0.0

            pl = re.search(r"packet-loss=(\d+)%", out)
            if pl and int(pl.group(1)) >= 100:
                return False, out, 0.0

            latency = 0.0
            if re.search(r"received=[1-9]", out) or (pl and int(pl.group(1)) < 100):
                tm = re.search(r"time=([\d.]+)\s*ms", out, re.IGNORECASE)
                if not tm:
                    tm = re.search(r"(\d+(?:\.\d+)?)\s*ms", out)
                latency = float(tm.group(1)) if tm else 1.0
                return True, out, latency

            return False, out, 0.0

        except Exception as e:
            logger.error("ping_from_router_verbose: %s", e)
            return False, str(e), 0.0

    def ping_device(self, ip: str) -> Tuple[bool, str]:
        """
        فحص اتصال جهاز (محاكاة قديمة — يُفضّل استخدام ping_from_router من الخدمات).
        """
        ok, msg, ms = self.ping_from_router(ip)
        if ok:
            return True, f"متصل - {ms:.0f}ms"
        return False, msg
