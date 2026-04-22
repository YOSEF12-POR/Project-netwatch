#!/usr/bin/env python3
"""
خدمة الأجهزة - إدارة أجهزة الشبكة ومسحها
"""
import logging
from typing import List, Dict, Tuple, Optional, Any
from datetime import datetime, timedelta
import time
import subprocess
import sys
import re
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.models.models import db, Device, Network
from app.utils.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

class DeviceService:
    """
    خدمة لإدارة أجهزة الشبكة
    """

    @staticmethod
    def get_network_devices(network_id: int) -> List[Dict[str, Any]]:
        """
        جلب جميع أجهزة الشبكة

        Args:
            network_id: معرف الشبكة

        Returns:
            List[Dict]: قائمة الأجهزة
        """
        try:
            devices = Device.query.filter_by(network_id=network_id).all()

            result = []
            for device in devices:
                result.append({
                    "id": device.id,
                    "name": device.name,
                    "ip": device.ip_address,
                    "mac": device.mac_address,
                    "type": device.device_type,
                    "description": device.description,
                    "status": device.status,
                    "last_ping": device.last_ping,
                    "last_updated": device.last_updated.isoformat() if device.last_updated else None,
                    "created_at": device.created_at.isoformat() if device.created_at else None
                })

            return result

        except Exception as e:
            logger.error(f"❌ خطأ في جلب الأجهزة: {e}")
            return []

    @staticmethod
    def discover_network_devices(
        network_id: int, interface: str, ip_range: str
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        اكتشاف الأجهزة فقط (بدون حفظ في قاعدة البيانات) — لعرض نتائج المسح كما هي.
        """
        try:
            logger.info(
                f"🔍 اكتشاف الشبكة {network_id} على {interface} في النطاق {ip_range}"
            )

            connection = ConnectionManager.get_connection(network_id)
            if not connection:
                return False, "❌ فشل الاتصال بالراوتر — أدخل بيانات MikroTik في الجلسة", []

            success, message, devices = connection.discover_devices(interface, ip_range)
            if not success:
                return False, message, []

            if not devices:
                logger.info(
                    "⚠️ لم يتم العثور على أجهزة عبر ARP/Neighbors، البدء بـ IP Scan على MikroTik"
                )
                sweep_ok, sweep_msg, pinged = connection.router_ping_sweep(
                    ip_range, limit=256
                )
                logger.info(
                    f"ping sweep result ok={sweep_ok} pinged={pinged} msg={sweep_msg}"
                )

                success2, message2, devices2 = connection.discover_devices(
                    interface, ip_range
                )
                if success2 and devices2:
                    devices = devices2
                    message = "تم اكتشاف الأجهزة بعد IP Scan على MikroTik"
                else:
                    message = "لم يتم العثور على أجهزة (حتى بعد IP Scan على MikroTik)"

            return True, message, devices

        except Exception as e:
            logger.error(f"❌ خطأ في اكتشاف الشبكة: {e}")
            return False, f"❌ خطأ في اكتشاف الشبكة: {str(e)}", []

    @staticmethod
    def scan_network(network_id: int, interface: str, ip_range: str) -> Tuple[bool, str, int]:
        """
        مسح الشبكة (عرض فقط): لا يحفظ في الجدول. استخدم commit_scan_results للحفظ مع Ping.
        """
        ok, msg, devices = DeviceService.discover_network_devices(
            network_id, interface, ip_range
        )
        if not ok:
            return False, msg, 0
        return True, msg, len(devices)

    @staticmethod
    def commit_scan_results(
        network_id: int, devices: List[Dict[str, Any]]
    ) -> Tuple[bool, str, int]:
        """
        حفظ نتائج المسح في الجدول مع تحديد UP/DOWN عبر ping من MikroTik.
        الأجهزة الموجودة مسبقاً: تحديث الحالة فقط (ومعلومات مساعدة)، بدون صف مكرر.
        """
        try:
            connection = ConnectionManager.get_connection(network_id)
            if not connection:
                return False, "❌ فشل الاتصال بالراوتر", 0

            changed = 0
            for d in devices or []:
                ip = (d.get("ip") or "").strip()
                if not ip:
                    continue

                up, _ping_msg, latency = connection.ping_from_router(ip)
                status = "online" if up else "offline"

                existing = Device.query.filter_by(
                    network_id=network_id, ip_address=ip
                ).first()
                if existing:
                    name = (d.get("name") or "").strip()
                    mac = (d.get("mac") or "").strip()
                    if name:
                        existing.name = name
                    if mac:
                        existing.mac_address = mac
                    existing.status = status
                    existing.last_ping = latency if up else 0
                    existing.last_updated = datetime.utcnow()
                    changed += 1
                else:
                    device = Device(
                        network_id=network_id,
                        name=(d.get("name") or f"Device-{ip}").strip(),
                        ip_address=ip,
                        mac_address=(d.get("mac") or "").strip() or None,
                        device_type=d.get("device_type") or "Unknown",
                        status=status,
                        last_ping=latency if up else 0,
                        last_updated=datetime.utcnow(),
                    )
                    db.session.add(device)
                    changed += 1

            db.session.commit()
            logger.info(f"✅ تم حفظ/تحديث {changed} جهاز مع حالة ping من الراوتر")
            return True, f"تم حفظ {changed} جهاز مع فحص ping من MikroTik", changed

        except Exception as e:
            logger.error(f"❌ خطأ في حفظ نتائج المسح: {e}")
            db.session.rollback()
            return False, f"❌ خطأ: {str(e)}", 0

    @staticmethod
    def _system_ping(ip: str, timeout_ms: int = 1000) -> Tuple[bool, int]:
        """
        Ping عبر أمر النظام (Windows/Linux) بدون صلاحيات raw sockets.
        Returns: (success, latency_ms)
        """
        try:
            if sys.platform.startswith("win"):
                cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
            else:
                # -c 1: مرة واحدة, -W بالثواني في أغلب التوزيعات
                cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), ip]

            r = subprocess.run(cmd, capture_output=True, text=True)
            ok = r.returncode == 0
            # محاولة استخراج الزمن من المخرجات
            out = (r.stdout or "") + "\n" + (r.stderr or "")
            m = None
            for pat in [r"time[=<]?\s*(\d+)\s*ms", r"Average\s*=\s*(\d+)\s*ms"]:
                mm = re.search(pat, out, re.IGNORECASE)
                if mm:
                    m = mm
                    break
            latency = int(m.group(1)) if m else (1 if ok else 0)
            return ok, latency
        except Exception:
            return False, 0

    @staticmethod
    def _read_arp_cache() -> Dict[str, str]:
        """
        قراءة ARP table من نظام التشغيل وربط IP -> MAC (إن توفر).
        """
        try:
            r = subprocess.run(["arp", "-a"], capture_output=True, text=True)
            out = (r.stdout or "") + "\n" + (r.stderr or "")
            table: Dict[str, str] = {}

            # Windows: "  192.168.1.10          aa-bb-cc-dd-ee-ff     dynamic"
            # Linux/mac: "192.168.1.10 ether aa:bb:cc:dd:ee:ff ..."
            win_pat = re.compile(r"(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s+(?P<mac>(?:[0-9a-fA-F]{2}[-:]){5}[0-9a-fA-F]{2})")
            for m in win_pat.finditer(out):
                ip = m.group("ip")
                mac = m.group("mac").replace("-", ":").lower()
                table[ip] = mac

            return table
        except Exception:
            return {}

    @staticmethod
    def _ping_sweep_discover(ip_range: str) -> List[Dict[str, Any]]:
        """
        اكتشاف الأجهزة عبر Ping sweep على الرينج.
        يعيد قائمة أجهزة بالشكل المتوقع لـ _upsert_discovered_devices().
        """
        try:
            net = ipaddress.ip_network(ip_range, strict=False)
        except Exception:
            return []

        # حماية من رينجات ضخمة
        hosts = list(net.hosts())
        if len(hosts) > 512:
            hosts = hosts[:512]

        ips = [str(h) for h in hosts]
        discovered: List[Dict[str, Any]] = []

        # نستخدم concurrency خفيفة حتى لا نعلق السيرفر
        max_workers = 64 if len(ips) > 64 else max(8, len(ips))
        results: Dict[str, Tuple[bool, int]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(DeviceService._system_ping, ip, 800): ip for ip in ips}
            for fut in as_completed(futs):
                ip = futs[fut]
                try:
                    ok, latency = fut.result()
                except Exception:
                    ok, latency = False, 0
                results[ip] = (ok, latency)

        arp = DeviceService._read_arp_cache()
        for ip, (ok, latency) in results.items():
            if not ok:
                continue
            discovered.append({
                "ip": ip,
                "mac": arp.get(ip, ""),
                "name": f"Device-{ip}",
                "source": "ping_sweep",
                "last_ping": latency,
                "status": "online",
            })

        return discovered

    @staticmethod
    def ping_device(
        network_id: int, device_ip: str, from_router: bool = False
    ) -> Tuple[bool, str]:
        """
        فحص اتصال جهاز.
        from_router=False: ping من خادم التطبيق.
        from_router=True: ping من MikroTik مباشرة.
        """
        try:
            device = Device.query.filter_by(
                network_id=network_id, ip_address=device_ip
            ).first()

            latency = 0.0
            if from_router:
                connection = ConnectionManager.get_connection(network_id)
                if not connection:
                    return False, "❌ لا يوجد اتصال بالراوتر"
                success, msg, latency = connection.ping_from_router(device_ip)
                output = (
                    f"من الراوتر: متصل ~{latency:.0f}ms"
                    if success
                    else f"من الراوتر: {msg}"
                )
            else:
                ok, lat = DeviceService._system_ping(device_ip, timeout_ms=1200)
                success = ok
                latency = float(lat)
                output = (
                    f"Ping محلي: متصل - {lat}ms"
                    if success
                    else "Ping محلي: غير متصل"
                )

            if device:
                device.status = "online" if success else "offline"
                device.last_ping = latency if success else 0
                device.last_updated = datetime.utcnow()
                db.session.commit()

            return success, output

        except Exception as e:
            logger.error(f"❌ خطأ في ping الجهاز: {e}")
            return False, f"❌ خطأ: {str(e)}"

    @staticmethod
    def ping_device_router_verbose(network_id: int, device_ip: str) -> Tuple[bool, str]:
        """
        Ping من MikroTik مع مخرجات كاملة (count=4) وتحديث حالة الجهاز في قاعدة البيانات.
        """
        try:
            connection = ConnectionManager.get_connection(network_id)
            if not connection:
                return False, "❌ لا يوجد اتصال بالراوتر"

            ok, output, latency = connection.ping_from_router_verbose(
                device_ip, count=4
            )
            device = Device.query.filter_by(
                network_id=network_id, ip_address=device_ip
            ).first()
            if device:
                device.status = "online" if ok else "offline"
                device.last_ping = latency if ok else 0
                device.last_updated = datetime.utcnow()
                db.session.commit()

            return ok, output or ""

        except Exception as e:
            logger.error(f"❌ خطأ في ping الجهاز (verbose): {e}")
            return False, f"❌ خطأ: {str(e)}"

    @staticmethod
    def refresh_all_devices_status(network_id: int) -> Tuple[int, int]:
        """
        تحديث حالة جميع الأجهزة عبر ping من MikroTik.
        """
        try:
            connection = ConnectionManager.get_connection(network_id)
            if not connection:
                logger.error("❌ لا يمكن تحديث الأجهزة بدون اتصال بالراوتر")
                return 0, 0

            devices = Device.query.filter_by(network_id=network_id).all()
            up_count = 0
            down_count = 0

            for device in devices:
                ok, _msg, latency = connection.ping_from_router(device.ip_address)
                device.status = "online" if ok else "offline"
                device.last_ping = latency if ok else 0
                device.last_updated = datetime.utcnow()
                if ok:
                    up_count += 1
                else:
                    down_count += 1

            db.session.commit()

            logger.info(
                f"✅ تم تحديث {len(devices)} جهاز من الراوتر - Up: {up_count}, Down: {down_count}"
            )
            return up_count, down_count

        except Exception as e:
            logger.error(f"❌ خطأ في تحديث الأجهزة: {e}")
            db.session.rollback()
            return 0, 0

    @staticmethod
    def add_manual_device(
        network_id: int,
        name: str,
        ip: str,
        mac: str = None,
        device_type: str = "Unknown",
        description: str = None,
    ) -> Tuple[bool, str]:
        """
        إضافة جهاز يدوياً (IP، MAC اختياري، اسم/وصف).
        """
        try:
            ip = (ip or "").strip()
            name = (name or "").strip()
            if not ip or not name:
                return False, "❌ الاسم وعنوان IP مطلوبان"

            existing = Device.query.filter_by(
                network_id=network_id, ip_address=ip
            ).first()
            if existing:
                return False, f"❌ جهاز بعنوان IP {ip} موجود بالفعل"

            mac_clean = (mac or "").strip() or None

            device = Device(
                network_id=network_id,
                name=name,
                ip_address=ip,
                mac_address=mac_clean,
                device_type=device_type or "Unknown",
                description=(description or "").strip() or None,
                status="unknown",
                last_ping=0,
                last_updated=datetime.utcnow(),
            )

            db.session.add(device)
            db.session.commit()

            logger.info(f"✅ تم إضافة الجهاز: {name} ({ip})")
            return True, f"✅ تم إضافة الجهاز {name} بنجاح"

        except Exception as e:
            logger.error(f"❌ خطأ في إضافة الجهاز: {e}")
            db.session.rollback()
            return False, f"❌ خطأ في إضافة الجهاز: {str(e)}"

    @staticmethod
    def delete_device(device_id: int) -> Tuple[bool, str]:
        """
        حذف جهاز

        Args:
            device_id: معرف الجهاز

        Returns:
            Tuple[bool, str]: (نجاح, رسالة)
        """
        try:
            device = Device.query.get(device_id)
            if not device:
                return False, "❌ الجهاز غير موجود"

            device_name = device.name
            db.session.delete(device)
            db.session.commit()

            logger.info(f"✅ تم حذف الجهاز: {device_name}")
            return True, f"✅ تم حذف الجهاز {device_name} بنجاح"

        except Exception as e:
            logger.error(f"❌ خطأ في حذف الجهاز: {e}")
            db.session.rollback()
            return False, f"❌ خطأ في حذف الجهاز: {str(e)}"
