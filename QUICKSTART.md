# NetWatch Dashboard - Phase 5 Quick Start Guide

## 🚀 Starting the Application

```bash
cd C:\Users\Lp\Project-netwatch
python run.py
```

**Expected Output:**
```
* Running on all addresses (0.0.0.0)
* Running on http://127.0.0.1:5000
* Running on http://10.10.10.213:5000
```

---

## 🌐 Access Points

| Component | URL | Status |
|-----------|-----|--------|
| **Login Page** | http://localhost:5000 | ✅ Working |
| **Dashboard** | http://localhost:5000/dashboard | ✅ WebSocket Ready |
| **API** | http://localhost:5000/api/ | ✅ Endpoints Ready |

---

## 📝 Testing Authentication

### Create Test Account
1. Open http://localhost:5000
2. Click "إنشاء حساب جديد" (Create New Account)
3. Enter:
   - Username: `testuser`
   - Email: `test@netwatch.local`
   - Password: `Test@123456`
   - Confirm: `Test@123456`
4. Click "تسجيل" (Register)

### Login
1. Use credentials:
   - Username: `testuser`
   - Password: `Test@123456`
2. You'll be redirected to dashboard

---

## 📊 Dashboard Features (After Login)

### Sidebar Navigation
- **المراقبة** (Monitoring)
  - Dashboard (Overview)
  - Devices (List)
  - Topology (Map)

- **الإدارة** (Administration)
  - Logs & Alerts
  - Add Device
  - Settings

### Dashboard Pages

#### 1. Overview
- 4 KPI Cards showing:
  - Network Status
  - Online Devices count
  - Internet Status
  - Active Alerts count
- Network Traffic Chart (coming with real data)
- Device Status Donut Chart (coming with real data)
- Quick Devices Table

#### 2. Devices
- Search devices by IP, name, or MAC
- Filter by status (All, Online, Warning, Offline)
- Sort by any column
- View device details

#### 3. Topology
- Network map visualization (placeholder, ready for real data)
- Device relationships
- Network topology display

#### 4. Logs & Alerts
- Recent alerts and system events
- Timestamp and status information
- Alert severity indicators

#### 5. Add Device
- Device registration form
- Support for MikroTik devices
- Automatic status detection

#### 6. Settings
- Notification configuration
- Monitoring schedule
- Alert thresholds

---

## 🔌 API Testing

### Query Dashboard Status (Requires Session)
```bash
# After logging in via browser, use the session cookie
Invoke-WebRequest -Uri "http://localhost:5000/api/dashboard/status" ` 
  -UseBasicParsing -SessionVariable "web"
```

### Get Device List
```bash
Invoke-WebRequest -Uri "http://localhost:5000/api/dashboard/devices" ` 
  -UseBasicParsing
```

### Get Recent Alerts
```bash
Invoke-WebRequest -Uri "http://localhost:5000/api/dashboard/alerts?limit=5" ` 
  -UseBasicParsing
```

### Add New Device
```bash
$body = @{
    name = "TestDevice"
    ip = "192.168.1.100"
    mac = "00:1A:2B:3C:4D:5E"
    type = "Router"
} | ConvertTo-Json

Invoke-WebRequest -Uri "http://localhost:5000/api/dashboard/devices/add" `
  -Method POST `
  -ContentType "application/json" `
  -Body $body `
  -UseBasicParsing
```

---

## 🔄 WebSocket Connection

The dashboard automatically connects to WebSocket when you log in. 

### Check WebSocket in Browser
1. Open DevTools (F12)
2. Go to Console tab
3. You should see messages like:
   ```
   WebSocket connected
   Received status update: {...}
   ```

### Real-time Updates
- Device status changes broadcast automatically
- New alerts appear in real-time
- Charts update as data arrives

---

## 🐛 Troubleshooting

### Application Won't Start
```bash
# Check dependencies
pip install -r requirements.txt

# Check Python version (3.8+)
python --version

# Check port availability
lsof -i :5000  # Check if port 5000 is in use
```

### Dashboard Doesn't Load
1. Clear browser cache (Ctrl+Shift+Del)
2. Hard refresh (Ctrl+F5)
3. Check browser console for JavaScript errors

### WebSocket Connection Issues
1. Check browser console (F12 → Console)
2. Look for connection errors
3. Verify you're logged in first

### Database Errors
```bash
# Check database connection in config.py
# Verify MySQL is running
# Check credentials in .env file
```

---

## 📱 Features Status

| Feature | Status | Notes |
|---------|--------|-------|
| Authentication | ✅ Ready | Login/Register working |
| Dashboard UI | ✅ Ready | 6 pages, professional design |
| WebSocket | ✅ Ready | Auto-reconnect enabled |
| API Endpoints | ✅ Ready | 6 endpoints functional |
| Database | ✅ Ready | Tables created automatically |
| MikroTik Connection | 📍 Pending | Credentials configured (203.213.6.173.121:6092, test/123) |
| Real-time Monitoring | 📍 Pending | Infrastructure ready, awaiting device data |
| Charts/Graphs | 📍 Pending | Chart.js configured, awaiting data |

---

## 🎨 Design Highlights

### Color Scheme
- **Dark Theme** - Easy on eyes for 24/7 monitoring
- **Professional Blues** - Calming yet informative
- **Arabic RTL** - Full support for right-to-left text

### Typography
- Tajawal (Arabic font) for labels
- Oxanium for headings
- IBM Plex Mono for technical data

### Responsive Design
- Works on desktop (1920px+)
- Tablet friendly (1024px+)
- Mobile compatible (hidden sidebar on small screens)

---

## 📞 Support

For issues or questions:
1. Check browser console (F12)
2. Check application logs
3. Review PHASE5_COMPLETION.md for technical details
4. Check requirements.txt for dependency versions

---

## 🔐 Security Notes

- Use strong passwords
- Never expose credentials in code
- Use HTTPS in production (configure in run.py)
- Implement 2FA for production (ready in todo list)
- Keep dependencies updated

---

**Ready to use! Happy monitoring! 🎉**

