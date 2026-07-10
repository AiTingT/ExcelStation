/* Common Scripts - 轻量在线工具箱 */

(function() {
  'use strict';

  // Theme management
  var ThemeManager = {
    get: function() {
      return localStorage.getItem('theme') || 'light';
    },
    set: function(theme) {
      document.documentElement.setAttribute('data-theme', theme);
      localStorage.setItem('theme', theme);
      this._syncIframes(theme);
      this._notify(theme);
    },
    toggle: function() {
      var cur = this.get();
      this.set(cur === 'dark' ? 'light' : 'dark');
    },
    init: function() {
      this.set(this.get());
    },
    _listeners: [],
    onChange: function(fn) {
      this._listeners.push(fn);
    },
    _notify: function(theme) {
      this._listeners.forEach(function(fn) { fn(theme); });
    },
    _syncIframes: function(theme) {
      try {
        var frames = document.querySelectorAll('iframe');
        for (var i = 0; i < frames.length; i++) {
          var frame = frames[i];
          if (frame.contentDocument && frame.contentDocument.documentElement) {
            frame.contentDocument.documentElement.setAttribute('data-theme', theme);
          }
        }
      } catch(e) {}
    }
  };

  // Toast
  function showToast(msg) {
    var toast = document.getElementById('toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'toast';
      toast.className = 'toast';
      document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(function() { toast.classList.remove('show'); }, 2000);
  }

  // Copy to clipboard
  function copyText(text, successMsg) {
    if (!text) { showToast('没有可复制的内容'); return; }
    navigator.clipboard.writeText(text).then(function() {
      showToast(successMsg || '已复制到剪贴板');
    });
  }

  // Sync theme from parent (for iframe pages)
  function syncParentTheme() {
    try {
      var parentTheme = window.parent.document.documentElement.getAttribute('data-theme');
      if (parentTheme) {
        document.documentElement.setAttribute('data-theme', parentTheme);
        localStorage.setItem('theme', parentTheme);
      }
    } catch(e) {}
  }

  // Listen for storage events (theme changes from parent window)
  function listenStorageTheme() {
    window.addEventListener('storage', function(e) {
      if (e.key === 'theme' && e.newValue) {
        document.documentElement.setAttribute('data-theme', e.newValue);
      }
    });
  }

  // Poll parent theme periodically as a fallback
  function startParentThemePolling() {
    setInterval(function() {
      try {
        var parentTheme = window.parent.document.documentElement.getAttribute('data-theme');
        var currentTheme = document.documentElement.getAttribute('data-theme');
        if (parentTheme && parentTheme !== currentTheme) {
          document.documentElement.setAttribute('data-theme', parentTheme);
          localStorage.setItem('theme', parentTheme);
        }
      } catch(e) {}
    }, 500);
  }

  // Format number (trim trailing zeros)
  function fmtNum(n, digits) {
    digits = digits || 4;
    return (n).toFixed(digits).replace(/\.?0+$/, '');
  }

  // Expose globals
  window.Toolbox = {
    ThemeManager: ThemeManager,
    showToast: showToast,
    copyText: copyText,
    syncParentTheme: syncParentTheme,
    listenStorageTheme: listenStorageTheme,
    startParentThemePolling: startParentThemePolling,
    fmtNum: fmtNum
  };

  // Auto setup on iframe pages
  if (window.self !== window.top) {
    ThemeManager.init();
    syncParentTheme();
    listenStorageTheme();
    startParentThemePolling();
  }
})();
