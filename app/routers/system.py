"""系统状态监控路由"""
import time
import psutil
from fastapi import APIRouter

router = APIRouter(prefix="/api/system", tags=["system"])

# 服务启动时间
_start_time = time.time()
# 上次磁盘 IO 计数
_last_disk_io = None


@router.get("/stats")
async def system_stats():
    """返回系统资源使用状态"""
    global _last_disk_io

    process = psutil.Process()
    mem_info = process.memory_info()
    sys_mem = psutil.virtual_memory()
    cpu_count = psutil.cpu_count() or 1
    cpu_percent = process.cpu_percent(interval=0.1)
    cpu_percent_norm = round(cpu_percent / cpu_count, 1)
    sys_cpu = psutil.cpu_percent(interval=0.1)

    # 磁盘 I/O（增量，每次查询相比上次的差值）
    try:
        disk_io = psutil.disk_io_counters()
        if _last_disk_io is not None and disk_io is not None:
            delta_read = max(0, disk_io.read_bytes - _last_disk_io.read_bytes)
            delta_write = max(0, disk_io.write_bytes - _last_disk_io.write_bytes)
            # 换算为 KB/s（前端每 3 秒轮询一次）
            read_kbps = round(delta_read / 1024 / 3, 1)
            write_kbps = round(delta_write / 1024 / 3, 1)
        else:
            read_kbps = 0
            write_kbps = 0
        _last_disk_io = disk_io
    except Exception:
        read_kbps = 0
        write_kbps = 0

    uptime = round(time.time() - _start_time, 0)

    return {
        "uptime": uptime,
        "process": {
            "memoryMB": round(mem_info.rss / 1024 / 1024, 1),
            "cpuPercent": round(cpu_percent, 1),
            "cpuPercentNorm": cpu_percent_norm,
            "cpuCores": cpu_count,
        },
        "system": {
            "memoryPercent": round(sys_mem.percent, 1),
            "memoryUsedGB": round(sys_mem.used / 1024 / 1024 / 1024, 1),
            "memoryTotalGB": round(sys_mem.total / 1024 / 1024 / 1024, 1),
            "cpuPercent": round(sys_cpu, 1),
        },
        "disk": {
            "readKBps": read_kbps,
            "writeKBps": write_kbps,
        },
    }
