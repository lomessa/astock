"""
命令行入口
==========
usage:
    python -m astock.cli.main scan          # 扫描今日信号
    python -m astock.cli.main plan          # 生成交易计划
    python -m astock.cli.main sentiment     # 市场情绪
    python -m astock.cli.main hold          # 查看持仓与预警
    python -m astock.cli.main buy SYMBOL    # 手动录入买入
    python -m astock.cli.main sell SYMBOL   # 手动录入卖出
    python -m astock.cli.main push          # 推送今日计划到手机
    python -m astock.cli.main realtime      # 启动实时交易（模拟盘）
"""
import asyncio
import signal
import sys
from datetime import datetime

import click
from colorama import init, Fore, Style

# 初始化 colorama
init(autoreset=True)

from astock.data.client import DataClient
from astock.strategy.zt_strategies import (
    FirstBoardStrategy,
    ContinuousBoardStrategy,
    ResealStrategy,
    AuctionStrategy,
    StrongStockPullbackStrategy,
)
from astock.signal.engine import SignalEngine
from astock.sentiment.market import SentimentAnalyzer
from astock.execution.portfolio import Portfolio
from astock.execution.plan import PlanManager
from astock.notify.notifier import Notifier
from astock.realtime.loop import build_loop_with_legacy_strategies


def _engine():
    data = DataClient()
    strategies = [
        FirstBoardStrategy(),
        ContinuousBoardStrategy(),
        ResealStrategy(),
        AuctionStrategy(),
        StrongStockPullbackStrategy(),
    ]
    # 加载 GP 因子增强器（如果存在已保存的因子）
    try:
        from astock.factor_mining.booster import GPFactorBooster
        gp_booster = GPFactorBooster()
    except Exception:
        gp_booster = None
    return SignalEngine(data=data, strategies=strategies, gp_booster=gp_booster)


@click.group()
def cli():
    """AStock - 个人短线打板量化框架"""
    pass


@cli.command()
@click.option("--date", default=None, help="指定日期 YYYYMMDD")
@click.option("--top", default=15, help="显示前 N 条")
def scan(date, top):
    """扫描今日交易信号"""
    click.echo(f"{Fore.CYAN}🔍 扫描信号...{Style.RESET_ALL}")
    engine = _engine()
    plans = engine.generate_plans(date)
    if not plans:
        click.echo(f"{Fore.YELLOW}今日无信号{Style.RESET_ALL}")
        return
    text = engine.format_plans(plans[:top])
    click.echo(text)


@cli.command()
@click.option("--date", default=None, help="指定日期 YYYYMMDD")
@click.option("--top", default=15, help="保存前 N 条")
def plan(date, top):
    """生成并保存交易计划"""
    click.echo(f"{Fore.CYAN}📋 生成交易计划...{Style.RESET_ALL}")
    engine = _engine()
    plans = engine.generate_plans(date)
    if not plans:
        click.echo(f"{Fore.YELLOW}今日无交易计划{Style.RESET_ALL}")
        return

    pm = PlanManager()
    pm.save_plans(plans[:top], date)
    click.echo(f"{Fore.GREEN}已保存 {len(plans[:top])} 条交易计划{Style.RESET_ALL}")
    click.echo(engine.format_plans(plans[:top]))


@cli.command()
@click.option("--date", default=None, help="指定日期 YYYYMMDD")
def sentiment(date):
    """查看市场情绪"""
    click.echo(f"{Fore.CYAN}🔥 分析市场情绪...{Style.RESET_ALL}")
    analyzer = SentimentAnalyzer()
    report = analyzer.analyze(date)
    click.echo(analyzer.format_report(report))


@cli.command()
def hold():
    """查看持仓与预警"""
    click.echo(f"{Fore.CYAN}💼 持仓概览{Style.RESET_ALL}")
    pf = Portfolio()
    pf.print_summary()


@cli.command()
@click.argument("symbol")
@click.option("--price", type=float, required=True, help="买入价格")
@click.option("--volume", type=int, required=True, help="买入股数")
@click.option("--stop-loss", type=float, default=0.0, help="止损价")
@click.option("--stop-profit", type=float, default=0.0, help="止盈价")
@click.option("--strategy", default="手动", help="来源策略")
@click.option("--name", default="", help="股票名称（可选）")
def buy(symbol, price, volume, stop_loss, stop_profit, strategy, name):
    """手动录入买入"""
    pf = Portfolio()
    if not name:
        # 尝试从行情获取名称
        try:
            df = pf.data.get_stock_info()
            row = df[df["symbol"] == symbol]
            if not row.empty:
                name = row.iloc[0]["name"]
        except Exception:
            pass
        if not name:
            name = symbol
    try:
        pf.buy(symbol, name, price, volume, stop_loss, stop_profit, strategy)
        click.echo(f"{Fore.GREEN}买入成功{Style.RESET_ALL}")
    except Exception as e:
        click.echo(f"{Fore.RED}买入失败: {e}{Style.RESET_ALL}")


@cli.command()
@click.argument("symbol")
@click.option("--price", type=float, required=True, help="卖出价格")
@click.option("--volume", type=int, default=None, help="卖出股数（默认全仓）")
def sell(symbol, price, volume):
    """手动录入卖出"""
    pf = Portfolio()
    try:
        pf.sell(symbol, price, volume)
        click.echo(f"{Fore.GREEN}卖出成功{Style.RESET_ALL}")
    except Exception as e:
        click.echo(f"{Fore.RED}卖出失败: {e}{Style.RESET_ALL}")


@cli.command()
@click.option("--date", default=None, help="指定日期")
def push(date):
    """推送交易计划到手机"""
    click.echo(f"{Fore.CYAN}📱 推送消息...{Style.RESET_ALL}")
    pm = PlanManager()
    plans = pm.load_plans(date)
    if not plans:
        click.echo(f"{Fore.YELLOW}无计划可推送，先生成计划: python -m astock.cli.main plan{Style.RESET_ALL}")
        return

    notifier = Notifier()
    # 只推送高置信度
    high_conf = [p for p in plans if p.confidence >= 0.6]
    if not high_conf:
        click.echo(f"{Fore.YELLOW}无高置信度信号，跳过推送{Style.RESET_ALL}")
        return

    title = f"AStock 交易计划 [{datetime.now().strftime('%m-%d')}]"
    lines = []
    for p in high_conf[:10]:
        lines.append(
            f"{p.action.value} {p.name}({p.symbol}) | {p.strategy} | 置信度{p.confidence:.0%}\n"
            f"买入价¥{p.entry_price} 止损¥{p.stop_loss} 止盈¥{p.stop_profit} 仓位{p.suggested_position:.0%}\n"
            f"理由: {p.reason}\n"
        )
    content = "\n".join(lines)
    ok = notifier.send(title, content)
    if ok:
        click.echo(f"{Fore.GREEN}推送成功{Style.RESET_ALL}")
    else:
        click.echo(f"{Fore.RED}推送失败，请检查 Webhook 配置{Style.RESET_ALL}")


@cli.command()
@click.option("--interval", default=5.0, help="行情轮询间隔（秒）")
@click.option("--paper/--live", default=True, help="模拟盘/实盘（默认模拟盘）")
def realtime(interval, paper):
    """启动实时交易循环（模拟盘）"""
    if not paper:
        click.echo(f"{Fore.RED}⚠️ 实盘模式暂未接入，请先使用 --paper 验证策略{Style.RESET_ALL}")
        return

    click.echo(f"{Fore.CYAN}🚀 启动实时交易（模拟盘）...{Style.RESET_ALL}")

    strategies = [
        FirstBoardStrategy(),
        ContinuousBoardStrategy(),
        ResealStrategy(),
        AuctionStrategy(),
        StrongStockPullbackStrategy(),
    ]
    loop = build_loop_with_legacy_strategies(strategies, poll_interval=interval)

    async def _run():
        await loop.start()
        try:
            # 一直运行到收盘或用户中断
            while loop._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await loop.stop()

    # 优雅退出
    def _sigint_handler(sig, frame):
        click.echo(f"\n{Fore.YELLOW}收到中断信号，正在停止...{Style.RESET_ALL}")
        if loop._task:
            loop._task.cancel()

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass



@cli.command()
@click.option("--date", default=None, help="截止日期 YYYYMMDD")
@click.option("--days", default=60, help="回看天数")
@click.option("--forward", default=3, help="预测未来 N 日收益率（默认 3）")
@click.option("--pop", default=100, help="种群大小")
@click.option("--gen", default=20, help="进化代数")
@click.option("--top", default=5, help="输出 Top N 因子")
def mine(date, days, forward, pop, gen, top):
    """遗传规划挖掘因子（默认预测 T+3）"""
    from astock.factor_mining.gp_engine import FactorGPEngine
    from astock.factor_mining.data_prep import prepare_data_from_db
    from astock.factor_mining.fitness import evaluate_ic
    from astock.factor_mining.factor_quality import evaluate_factor_quality, format_quality_report
    from deap import gp

    if date is None:
        date = datetime.now().strftime("%Y%m%d")

    click.echo(f"{Fore.CYAN}📊 准备数据 [{date}] 回看 {days} 天 预测 T+{forward}...{Style.RESET_ALL}")
    data_dict, label_df = prepare_data_from_db(date, lookback_days=days, forward_period=forward)
    if data_dict is None:
        click.echo(f"{Fore.RED}数据准备失败，请检查本地数据库{Style.RESET_ALL}")
        return

    click.echo(
        f"{Fore.GREEN}数据就绪: {data_dict['CLOSE'].shape[0]} 天 × "
        f"{data_dict['CLOSE'].shape[1]} 只股票{Style.RESET_ALL}"
    )
    click.echo(f"{Fore.CYAN}🧬 启动遗传规划 (种群={pop}, 代数={gen})...{Style.RESET_ALL}")

    engine = FactorGPEngine(
        data_dict, label_df, pop_size=pop, generations=gen
    )
    pop, log, hof = engine.run(verbose=True)

    click.echo(f"\n{Fore.YELLOW}{'='*60}{Style.RESET_ALL}")
    click.echo(f"{Fore.YELLOW}🏆 最优因子 Top {top}{Style.RESET_ALL}")
    for i, ind in enumerate(hof[:top], 1):
        expr = str(ind)
        mean_ic, ir, _ = evaluate_ic(
            gp.compile(expr=ind, pset=engine.pset), label_df
        )
        direction = "正向" if mean_ic > 0 else "反向"
        click.echo(
            f"\n{Fore.GREEN}[{i}]{Style.RESET_ALL} {expr}\n"
            f"    IC={mean_ic:.4f}  IR={ir:.4f}  "
            f"Fitness={ind.fitness.values[0]:.4f}  方向:{direction}"
        )

    # 质量体检
    click.echo(f"\n{Fore.CYAN}📋 最优因子质量体检...{Style.RESET_ALL}")
    best_factor = gp.compile(expr=hof[0], pset=engine.pset)
    quality = evaluate_factor_quality(best_factor, label_df)
    click.echo(format_quality_report(quality))

    click.echo(f"\n{Fore.CYAN}提示: 可将表达式保存到策略中使用{Style.RESET_ALL}")

# 兼容 python main.py 直接运行
if __name__ == "__main__":
    cli()
