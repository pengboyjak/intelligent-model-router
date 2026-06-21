"""
Model Router 使用示例
=====================

展示各种场景下的模型路由使用方式。

运行前需设置: export ANTHROPIC_API_KEY=your-key
"""

import asyncio
import os
from router import (
    BudgetLevel,
    IntelligentModelRouter,
    SubagentResult,
)


async def example_1_auto_routing():
    """示例 1: 全自动路由 —— 系统自动检测任务并选择模型"""
    print("\n" + "=" * 60)
    print("示例 1: 全自动路由")
    print("=" * 60)

    router = IntelligentModelRouter()

    tasks = [
        # 这些任务会自动路由到不同模型
        "设计一个电商系统的微服务架构，包括用户服务、订单服务、库存服务",
        "把变量名 maxRetryCount 改为 maxRetries",
        "审查这段代码的安全性: def transfer(from, to, amount): ...",
        "帮我写一个用户注册功能的单元测试",
        "给这个 API 接口写文档",
    ]

    for task in tasks:
        result = await router.execute(task)
        model = result.model_used.split("-")[-1] if "-" in result.model_used else result.model_used
        print(f"  任务: {task[:40]}...")
        print(f"  → 模型: {model}, 成功: {result.success}, 耗时: {result.elapsed_ms:.0f}ms")
        print(f"  → 输出预览: {result.output[:100]}...\n")


async def example_2_budget_control():
    """示例 2: 预算控制 —— 根据预算自动调整模型选择"""
    print("\n" + "=" * 60)
    print("示例 2: 预算控制")
    print("=" * 60)

    router = IntelligentModelRouter()
    task = "帮我重构这个支付模块，提高代码可读性和可维护性"

    for budget in [BudgetLevel.HIGH, BudgetLevel.NORMAL, BudgetLevel.LOW]:
        result = await router.execute(task, budget=budget)
        print(f"  预算={budget.value} → 模型={result.model_used}, effort={result.task_id.split('model=')[-1] if 'model=' in result.task_id else 'unknown'}")
        print(f"  token: {result.usage.get('input_tokens', 0)} → {result.usage.get('output_tokens', 0)}")
        print()


async def example_3_parallel_execution():
    """示例 3: 并行执行 —— 同时处理多个独立任务"""
    print("\n" + "=" * 60)
    print("示例 3: 并行执行")
    print("=" * 60)

    router = IntelligentModelRouter()

    tasks = [
        "审查 auth_service.py 的安全漏洞",
        "审查 payment_gateway.py 的事务一致性",
        "审查 user_repository.py 的查询性能",
        "审查 cache_manager.py 的缓存策略",
    ]

    start = asyncio.get_event_loop().time()
    results = await router.execute_parallel(tasks)
    elapsed = asyncio.get_event_loop().time() - start

    print(f"\n  并行执行 {len(tasks)} 个任务，总耗时: {elapsed:.1f}s")
    for task, r in zip(tasks, results):
        print(f"  [{r.model_used.split('-')[-1]}] {r.success and '✓' or '✗'} "
              f"{task[:50]}... ({r.elapsed_ms:.0f}ms)")


async def example_4_force_model():
    """示例 4: 强制指定模型 —— 覆盖自动路由"""
    print("\n" + "=" * 60)
    print("示例 4: 强制指定模型")
    print("=" * 60)

    router = IntelligentModelRouter()
    task = "写一个 Python 快速排序实现"

    models = ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"]
    for model in models:
        result = await router.execute(task, force_model=model)
        short_name = model.split("-")[-1]
        print(f"  强制={short_name:6s} → token: {result.usage.get('output_tokens', 0)}, "
              f"耗时: {result.elapsed_ms:.0f}ms")


async def example_5_cascading():
    """示例 5: 级联路由 —— 前一步的输出作为后一步的输入"""
    print("\n" + "=" * 60)
    print("示例 5: 级联路由")
    print("=" * 60)

    router = IntelligentModelRouter()

    # Step 1: 用 Opus 设计架构
    print("  [Step 1] 架构设计 → Opus")
    arch_result = await router.execute(
        "设计一个博客系统的数据库 schema，包括用户、文章、评论、标签表",
        force_model="claude-opus-4-7",
    )
    schema = arch_result.output
    print(f"  生成 schema: {len(schema)} 字符")

    # Step 2: 用 Sonnet 根据 schema 生成 ORM 代码
    print("  [Step 2] ORM 代码生成 → Sonnet")
    orm_result = await router.execute(
        f"根据以下数据库 schema 生成 SQLAlchemy ORM 模型代码:\n\n{schema}",
        force_model="claude-sonnet-4-6",
    )
    print(f"  生成 ORM 代码: {len(orm_result.output)} 字符")

    # Step 3: 用 Haiku 检查格式
    print("  [Step 3] 格式检查 → Haiku")
    format_result = await router.execute(
        f"检查这段代码的 PEP 8 格式规范:\n\n{orm_result.output[:2000]}",
        force_model="claude-haiku-4-5",
    )
    print(f"  格式检查: {len(format_result.output)} 字符")


async def example_6_custom_context():
    """示例 6: 提供精准上下文 —— 不给子代理无关信息"""
    print("\n" + "=" * 60)
    print("示例 6: 精准上下文")
    print("=" * 60)

    router = IntelligentModelRouter()

    # 只传递相关代码片段，不传递整个项目
    result = await router.execute(
        task="找出这段代码中的性能问题并优化",
        relevant_code="""
def process_orders(orders):
    results = []
    for order in orders:
        user = db.query(User).filter(User.id == order.user_id).first()
        items = db.query(Item).filter(Item.order_id == order.id).all()
        for item in items:
            product = db.query(Product).filter(Product.id == item.product_id).first()
            results.append({
                'order_id': order.id,
                'user': user.name,
                'product': product.name,
                'quantity': item.quantity
            })
    return results
""",
        system_instruction="输出优化后的代码，并简要说明优化点",
    )

    print(f"  模型: {result.model_used.split('-')[-1]}")
    print(f"  输出: {result.output[:300]}...")


async def main():
    print("Model Router 使用示例")
    print("=" * 60)

    await example_1_auto_routing()
    await example_2_budget_control()
    await example_3_parallel_execution()
    await example_4_force_model()
    await example_5_cascading()
    await example_6_custom_context()

    print("\n所有示例执行完毕!")


if __name__ == "__main__":
    asyncio.run(main())
