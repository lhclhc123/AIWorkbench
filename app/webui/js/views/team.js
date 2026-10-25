// views/team.js —— 团队（占位视图，团队已收进对话页顶部 TeamBar）
'use strict';

export function init(root) {
  if (!root.dataset.built) {
    root.innerHTML = `
      <div class="page-head">
        <h2>团队</h2>
      </div>
      <div class="card">
        <div class="card-title">团队</div>
        <div class="empty">团队协作已收进对话页顶部的 TeamBar，这里暂为占位</div>
      </div>
    `;
    root.dataset.built = '1';
  }
}
