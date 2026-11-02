<script setup>
defineProps({ detail: Object })
function modeLabel(mode) { return mode === 'multi' ? '多智能体' : '单智能体' }
function domainLabel(domain) {
  return { product: '商品', order: '订单', logistics: '物流', aftersales: '售后', policy: '政策', points: '积分' }[domain] || domain
}
</script>
<template>
  <div class="panel inspection">
    <template v-if="detail">
      <div class="panel-title"><h2>Run 详情</h2><span>{{ modeLabel(detail.run.execution_mode) }} · {{ detail.run.state }}</span></div>
      <dl>
        <dt>耗时与 Token</dt><dd>{{ detail.run.latency_ms || 0 }} ms · 输入 {{ detail.run.input_tokens || 0 }} · 输出 {{ detail.run.output_tokens || 0 }}</dd>
        <dt>版本</dt><dd>{{ detail.run.execution_trace?.graph_version || detail.run.prompt_version }}</dd>
        <template v-if="detail.run.execution_trace?.route"><dt>前置路由</dt><dd>{{ detail.run.execution_trace.route.route_kind }} · {{ detail.run.execution_trace.route.domains.map(domainLabel).join('、') }}<p>{{ detail.run.execution_trace.route.reason }}</p></dd></template>
      </dl>
      <details v-if="detail.run.execution_trace"><summary>查看计划、共享预算与降级原因</summary><pre>{{ JSON.stringify(detail.run.execution_trace, null, 2) }}</pre></details>
      <h3 v-if="detail.tasks?.length">领域任务与依赖</h3>
      <article v-for="task in detail.tasks || []" :key="task.id" class="task-card">
        <div class="panel-title"><strong>{{ task.task_id }} · {{ domainLabel(task.domain) }} Agent</strong><span>{{ task.state }}</span></div>
        <p>{{ task.skill_code }} · 依赖 {{ task.depends_on.join('、') || '无，可独立执行' }}</p>
        <p>{{ task.latency_ms || 0 }} ms · {{ task.model_calls || 0 }} 次模型调用 · 输入 {{ task.input_tokens }} / 输出 {{ task.output_tokens }} Token</p>
        <p v-if="task.error" class="alert error">{{ task.error }}</p>
        <details><summary>任务输入与领域结果</summary><pre>{{ JSON.stringify({ input: task.input_context, result: task.result }, null, 2) }}</pre></details>
        <article v-for="call in detail.tool_calls.filter(item => item.task_id === task.id)" :key="call.tool_call_id" class="tool-call">
          <strong>{{ call.tool_name }}</strong><small>{{ call.latency_ms }} ms · {{ call.success === true ? '调用成功' : call.success === false ? '调用失败' : '执行中' }}</small>
          <pre>{{ JSON.stringify(call.arguments, null, 2) }}</pre><details><summary>原始工具证据</summary><pre>{{ JSON.stringify(call.result, null, 2) }}</pre></details>
        </article>
      </article>
      <h3>最终结果</h3><pre>{{ JSON.stringify(detail.run.outcome, null, 2) }}</pre>
      <p v-if="detail.run.error" class="alert error">{{ detail.run.error }}</p>
      <template v-if="detail.tool_calls.some(item => !item.task_id)"><h3>工具调用</h3><article v-for="call in detail.tool_calls.filter(item => !item.task_id)" :key="call.tool_call_id" class="tool-call"><strong>{{ call.tool_name }}</strong><small>{{ call.latency_ms }} ms · {{ call.success ? '调用成功' : '调用失败' }}</small><pre>{{ JSON.stringify(call.arguments, null, 2) }}</pre><details><summary>原始工具证据</summary><pre>{{ JSON.stringify(call.result, null, 2) }}</pre></details></article></template>
    </template>
    <div v-else class="empty large"><strong>选择一次 Run</strong><span>查看执行模式、任务依赖、最终结果与工具证据</span></div>
  </div>
</template>
<style scoped>
.task-card{margin:18px;padding:14px;border:1px solid var(--line-hot);border-radius:4px;background:var(--panel-2);color:var(--ink)}
.task-card .panel-title{min-height:42px;padding:8px;gap:12px}.task-card p{font-size:12px;line-height:1.7;color:var(--muted)}
.task-card .tool-call{margin:12px 0}.task-card .tool-call small{float:none;display:block;margin-top:6px}
.inspection>details{margin:12px 18px}
pre{max-width:100%;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer;margin:12px 0}dd{overflow-wrap:anywhere}
</style>
