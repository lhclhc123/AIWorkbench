// views/bg.js —— 并行任务池（占位视图，后端暂无对应 RPC）
'use strict';

export function init(root) {
  if (!root.dataset.built) {
    root.innerHTML = `
      <div class="page-head">
        <h2>并行任务池</h2>
      </div>
      <div class="card">
        <div class="card-title">并行任务池</div>
        <div class="empty">并行任务池的实时状态暂未接入 Web 视图</div>
      </div>
    `;
    root.dataset.built = '1';
  }
}
