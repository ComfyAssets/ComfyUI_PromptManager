// pm-virtual-table.js
// Tiny virtualized table scroller helper for large lists (no deps)
// Usage: import and call PMVirtualTable.mount(container, { rowHeight, render })

(() => {
  if (typeof window === 'undefined') {
    return;
  }
  const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
  if (!pmActive) {
    console.info('[PromptManager] pm-virtual-table skipped outside PromptManager UI context');
    return;
  }
  const PMVirtualTable = {
    mount(container, { rowHeight = 40, total = 0, render }) {
      const scroller = document.createElement('div');
      const viewport = document.createElement('div');
      const content = document.createElement('div');
      scroller.style.position = 'relative';
      viewport.style.overflow = 'auto';
      viewport.style.maxHeight = '60vh';
      content.style.position = 'relative';
      content.style.willChange = 'transform';
      viewport.appendChild(content);
      scroller.appendChild(viewport);
      container.innerHTML = '';
      container.appendChild(scroller);

      function update(newTotal) {
        if (typeof newTotal === 'number') total = newTotal;
        const height = total * rowHeight;
        content.style.height = height + 'px';
        draw();
      }

      function draw() {
        const scrollTop = viewport.scrollTop;
        const vh = viewport.clientHeight;
        const start = Math.max(0, Math.floor(scrollTop / rowHeight) - 5);
        const end = Math.min(total, Math.ceil((scrollTop + vh) / rowHeight) + 5);
        // recycle children
        content.innerHTML = '';
        for (let i = start; i < end; i++) {
          const y = i * rowHeight;
          const row = render(i);
          row.style.position = 'absolute';
          row.style.top = y + 'px';
          row.style.left = 0;
          row.style.right = 0;
          content.appendChild(row);
        }
      }

      viewport.addEventListener('scroll', draw);
      window.addEventListener('resize', draw);
      update(total);
      return { update };
    }
  };

  window.PMVirtualTable = PMVirtualTable;
})();
