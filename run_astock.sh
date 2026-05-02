#!/bin/bash
# AStock 每日盘前策略自动运行脚本
# ================================
# 用法: ./run_astock.sh
# 由 crontab 每天早上 7:00 触发（周一到周五）

set -euo pipefail

# 项目目录
PROJECT_DIR="/root/workspace/astock"
LOG_DIR="${PROJECT_DIR}/logs"
VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python"
MAIN_SCRIPT="${PROJECT_DIR}/main.py"

# 日志文件（按日期命名）
TODAY=$(date +%Y%m%d)
LOG_FILE="${LOG_DIR}/astock_${TODAY}.log"

# 创建日志目录
mkdir -p "${LOG_DIR}"

# 判断今天是否是周一到周五（A股交易日）
DOW=$(date +%u)  # 1=周一, 5=周五, 6=周六, 7=周日
if [ "${DOW}" -gt 5 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 今天是周末，跳过运行" >> "${LOG_FILE}"
    exit 0
fi

# 运行策略，输出到日志（带时间戳）
{
    echo "========================================"
    echo "AStock 盘前策略运行"
    echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "========================================"
    echo ""

    cd "${PROJECT_DIR}"

    if [ ! -f "${VENV_PYTHON}" ]; then
        echo "错误: 虚拟环境 Python 不存在: ${VENV_PYTHON}"
        exit 1
    fi

    if [ ! -f "${MAIN_SCRIPT}" ]; then
        echo "错误: 主脚本不存在: ${MAIN_SCRIPT}"
        exit 1
    fi

    # 运行策略
    "${VENV_PYTHON}" "${MAIN_SCRIPT}"

    echo ""
    echo "========================================"
    echo "运行结束: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "========================================"
} >> "${LOG_FILE}" 2>&1

# 保留最近30天的日志
find "${LOG_DIR}" -name "astock_*.log" -type f -mtime +30 -delete

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 策略运行完成，日志: ${LOG_FILE}"
