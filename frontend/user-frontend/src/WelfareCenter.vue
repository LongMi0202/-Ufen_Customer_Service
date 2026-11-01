<script setup>
import { onMounted, ref } from 'vue'
import { commerceApi, newId } from './api'
const props = defineProps({ token: String, productId: String })
const emit = defineEmits(['navigate'])
const account = ref(null), transactions = ref([]), expiry = ref(null), quote = ref(null), product = ref(null)
const error = ref(''), busy = ref(false), address = ref(''), quantity = ref(1)
let fingerprint = '', operationKey = ''
function unwrap(r) { if (!r.success) throw new Error(r.message); return r.data }
function time(value) { return new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) }
function kind(value) { return { grant: '福利发放', spend: '兑换消费', refund: '积分退回', expire: '过期失效' }[value] || value }
async function refreshQuote() {
  error.value = ''
  try { quote.value = unwrap(await commerceApi.quote(props.token, props.productId, quantity.value)) }
  catch (e) { error.value = e.message }
}
async function redeem() {
  if (busy.value || !quote.value?.eligible) return
  if (!confirm(`确认使用 ${quote.value.required_points} 积分兑换？提交时会再次校验。`)) return
  busy.value = true; error.value = ''
  const payload = { product_id: props.productId, quantity: quantity.value, address: address.value }
  const serialized = JSON.stringify(payload)
  if (fingerprint !== serialized) { fingerprint = serialized; operationKey = newId('redeem') }
  try {
    const order = unwrap(await commerceApi.redeem(props.token, payload, operationKey))
    emit('navigate', `/me/orders/${order.id}`)
  } catch (e) { error.value = e.message; await refreshQuote() }
  finally { busy.value = false }
}
onMounted(async () => {
  try {
    if (props.productId) {
      product.value = unwrap(await commerceApi.product(props.token, props.productId))
      await refreshQuote()
    } else {
      const values = await Promise.all([commerceApi.points(props.token), commerceApi.transactions(props.token), commerceApi.expiring(props.token)])
      account.value = unwrap(values[0]); transactions.value = unwrap(values[1]); expiry.value = unwrap(values[2])
    }
  } catch (e) { error.value = e.message }
})
</script>

<template>
  <div class="welfare-center">
    <div v-if="error" class="alert error" role="alert">{{ error }}</div>
    <template v-if="productId">
      <header class="page-header"><div><p class="eyebrow">REDEEM BENEFITS</p><h1>确认积分兑换</h1><p>{{ product?.name }}</p></div></header>
      <form class="panel redemption" @submit.prevent="redeem">
        <label>数量<input v-model.number="quantity" type="number" min="1" max="10" required @change="refreshQuote" /></label>
        <div v-if="quote" class="quote"><p>所需积分 <strong>{{ quote.required_points }}</strong></p><p>可用积分 {{ quote.available_points }}</p><p>{{ quote.eligible ? '当前可以兑换' : quote.reasons.join('；') }}</p><small>{{ quote.notice }}</small></div>
        <label>收货地址<textarea v-model="address" minlength="5" maxlength="300" required placeholder="请填写完整收货地址" /></label>
        <button :disabled="busy || !quote?.eligible">{{ busy ? '提交中…' : '确认兑换' }}</button>
      </form>
    </template>
    <template v-else>
      <header class="page-header"><div><p class="eyebrow">MY BENEFIT POINTS</p><h1>我的积分</h1><p>仅显示当前福利项目账户；时间按北京时间显示。</p></div></header>
      <section v-if="account" class="points-stats"><article class="panel"><small>可用积分</small><strong>{{ account.available_points }}</strong><p>{{ account.status === 'active' ? '账户正常' : account.status }}</p></article><article class="panel"><small>未来30天到期</small><strong>{{ account.expiring_points }}</strong><p>优先使用最早到期的积分</p></article></section>
      <article class="panel"><h2>临期积分批次</h2><p v-if="!expiry?.batches.length">未来30天暂无临期积分。</p><div v-for="batch in expiry?.batches || []" :key="batch.batch_id" class="line-item"><span>{{ batch.source }}<small>{{ time(batch.expires_at) }} 到期</small></span><b>{{ batch.points }} 积分</b></div></article>
      <article class="panel"><h2>最近积分流水</h2><p v-if="!transactions.length">暂无记录。</p><div v-for="item in transactions" :key="item.id" class="line-item"><span><strong>{{ kind(item.kind) }}</strong><small>{{ item.reason }} · {{ item.order_id || item.id }} · {{ time(item.created_at) }}</small></span><b>{{ item.delta > 0 ? '+' : '' }}{{ item.delta }}</b></div><small>最多显示最近30条，历史来源以具体流水为准。</small></article>
    </template>
  </div>
</template>

<style scoped>
.redemption button{border:1px solid var(--cyan);padding:12px 18px;color:#041318;background:var(--cyan);font-weight:800;cursor:pointer}.redemption button:disabled{opacity:.5;cursor:not-allowed}.redemption button:hover:not(:disabled){background:#8cf2f9}
.welfare-center{display:grid;gap:24px}.points-stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.points-stats strong{display:block;font-size:38px;margin:10px 0;color:var(--cyan)}.redemption{max-width:680px;display:grid;gap:22px}.redemption label{display:grid;gap:8px}.redemption input,.redemption textarea{padding:12px;border:1px solid var(--line);color:var(--ink);background:var(--surface-2)}.quote{padding:18px;background:var(--surface-2);border:1px solid var(--line)}.quote strong{font-size:26px;color:var(--cyan)}.line-item small{display:block;margin-top:6px;line-height:1.6}.panel{overflow-wrap:anywhere}@media(max-width:600px){.points-stats{grid-template-columns:1fr}}
</style>
