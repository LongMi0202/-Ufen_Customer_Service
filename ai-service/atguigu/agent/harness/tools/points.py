from typing import Annotated
from urllib.parse import quote
from langchain.tools import ToolRuntime, tool
from atguigu.agent.harness.run.runtime import AgentRuntimeContext
from atguigu.agent.harness.tools.executor import ToolExecutor
from atguigu.agent.harness.tools.output import (PointsAccountData, PointsTransactionData,
    ExpiringPointsData, PointsRefundData, RedemptionQuoteData)
from atguigu.infrastructure.client import EcommerceClient

executor = ToolExecutor()


async def query(runtime, name, path, parameters, schema):
    if runtime.tool_call_id is None: raise RuntimeError("工具调用缺少编号")
    async def operation():
        return await EcommerceClient(runtime.context.access_token).get_json(path, query_params=parameters or None)
    return await executor.execute(runtime_context=runtime.context, tool_call_id=runtime.tool_call_id,
        tool_name=name, arguments=parameters, operation=operation, output_schema=schema)


@tool
async def get_points_account(runtime: ToolRuntime[AgentRuntimeContext]) -> str:
    """查询当前授权员工及福利项目的积分账户、可用余额、未来30天临期总量。"""
    return await query(runtime,"get_points_account","/points/account",{},PointsAccountData)


@tool
async def list_points_transactions(runtime: ToolRuntime[AgentRuntimeContext],
        order_id: Annotated[str | None,"可选订单编号，用于查看关联消费、退分或过期流水"] = None) -> str:
    """查询当前积分账户最近30条流水，区分发放、消费、退回与过期；不是完整历史汇总。"""
    params={"order_id":order_id.strip().upper().replace("-","_")} if order_id else {}
    return await query(runtime,"list_points_transactions","/points/transactions",params,list[PointsTransactionData])


@tool
async def get_expiring_points(runtime: ToolRuntime[AgentRuntimeContext],
        days: Annotated[int,"未来天数，范围1至366；默认30天"] = 30) -> str:
    """查询当前项目未来指定天数内的临期积分批次、数量及准确到期时间。"""
    return await query(runtime,"get_expiring_points","/points/expiring",{"days":days},ExpiringPointsData)


@tool
async def get_points_refund_status(order_id: Annotated[str,"已由用户或订单查询工具提供的订单编号"],
        runtime: ToolRuntime[AgentRuntimeContext]) -> str:
    """查询订单独立退分状态、应退/已退积分、退回时失效及可用部分、关联流水编号；需要流水明细时另查list_points_transactions。已退总额不等于当前可用余额，不执行退款。"""
    value=order_id.strip().upper().replace("-","_")
    return await query(runtime,"get_points_refund_status",f"/points/refunds/{quote(value,safe='')}",{"order_id":value},PointsRefundData)


@tool
async def quote_redemption(product_id: Annotated[str,"来自当前项目商品查询的商品编号"],
        runtime: ToolRuntime[AgentRuntimeContext], quantity: Annotated[int,"兑换数量，1至10"] = 1) -> str:
    """业务端计算兑换积分、资格、余额库存及限额，返回预检结论；不扣分也不锁库存。"""
    return await query(runtime,"quote_redemption","/redemptions/quote",
        {"product_id":product_id.strip().upper().replace("-","_"),"quantity":quantity},RedemptionQuoteData)
