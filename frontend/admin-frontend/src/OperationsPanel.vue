<script setup>
import { onMounted, ref } from 'vue'
import { adminApi } from './api'
const props = defineProps({ token: String })
const orders = ref([]), afterSales = ref([]), error = ref(''), notice = ref(''), busy = ref(false)
function refundStatus(value) { return { not_requested: '未申请退分', pending: '待人工核对', completed: '退分完成' }[value] || value }
function afterSaleKind(value) { return { refund: '退款', return: '退货退款', exchange: '换货' }[value] || value }
function afterSaleStatus(value) { return { pending: '待审核', processing: '处理中', approved: '已批准', completed: '已完成', rejected: '已拒绝' }[value] || value }
async function load() {
  try {
    const [a,b] = await Promise.all([adminApi.orders(props.token), adminApi.afterSales(props.token)])
    orders.value = a.data || []; afterSales.value = b.data || []
  } catch (e) { error.value = e.message }
}
async function refund(order) {
  if (!confirm(`确认已人工核对订单 ${order.id} 的取消或退款退货凭证，并完成整单退分？`)) return
  busy.value = true; error.value = ''; notice.value = ''
  try { const r = await adminApi.refund(props.token, order.id); notice.value = r.message; await load() }
  catch (e) { error.value = e.message }
  finally { busy.value = false }
}
onMounted(load)
</script>
<template>
  <section class="panel operations-panel"><div class="panel-title"><h2>订单与退分处理</h2><button class="ghost" @click="load">刷新</button></div><p>仅显示当前企业和福利项目。请人工核对取消或退款退货依据后再处理；重复操作不会重复退分。</p>
    <p v-if="error" class="alert error">{{ error }}</p><p v-if="notice" class="alert success">{{ notice }}</p>
    <div class="operation-scroll"><table>
      <thead><tr><th>订单 / 员工</th><th>订单状态</th><th>消耗积分</th><th>退分状态</th><th>处理</th></tr></thead>
      <tbody><tr v-for="order in orders" :key="order.id">
        <td>{{ order.id }}<small>{{ order.user_id }}</small></td><td>{{ order.status_label }}</td><td>{{ order.total_points }}</td>
        <td>{{ refundStatus(order.refund.status) }}<small>已退 {{ order.refund.returned_points }} / 应退 {{ order.refund.expected_points }}</small><small v-if="order.refund.status === 'completed'">退回时可用 {{ order.refund.usable_returned_points }} · 过期失效 {{ order.refund.expired_points }}</small><small>{{ order.refund.reason }}</small></td>
        <td><button v-if="order.refund.status === 'pending' || (order.status !== 'refunded' && afterSales.some(a => a.order_id === order.id && a.kind !== 'exchange' && ['pending','processing','approved'].includes(a.status)))" :disabled="busy" @click="refund(order)">人工确认退分</button></td>
      </tr></tbody>
    </table></div>
    <p>退回时可用积分仍沿用原批次有效期；员工当前可用余额以积分账户为准。</p>
    <h3>售后申请</h3><p v-if="!afterSales.length">暂无售后申请。</p><article v-for="a in afterSales" :key="a.id"><strong>{{ a.id }} · {{ a.order_id }}</strong><p>{{ afterSaleKind(a.kind) }} / {{ afterSaleStatus(a.status) }} · {{ a.reason }}</p></article>
  </section>
</template>
<style scoped>.operations-panel{display:grid;gap:18px}.operation-scroll{overflow:auto}table{width:100%;min-width:680px}td,th{padding:14px;text-align:left;border-bottom:1px solid #e5e9e6}small{display:block;margin-top:6px;color:#6b7972}article{padding:15px;border-bottom:1px solid #e5e9e6}</style>
