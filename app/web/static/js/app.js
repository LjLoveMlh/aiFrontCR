// aiFrontCR 知识库后台 - 通用 JS（占位，留作扩展）

document.addEventListener('DOMContentLoaded', () => {
  // 自动消失 alert
  document.querySelectorAll('[role="alert"]').forEach(el => {
    setTimeout(() => {
      el.style.transition = 'opacity 0.5s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 500);
    }, 5000);
  });
});
