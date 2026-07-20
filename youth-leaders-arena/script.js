(function () {
  var root = document.documentElement;
  var toggleBtn = document.getElementById('theme-toggle');

  function getSavedTheme() {
    try {
      return localStorage.getItem('yla-theme') || 'light';
    } catch (e) {
      return 'light';
    }
  }

  function saveTheme(theme) {
    try {
      localStorage.setItem('yla-theme', theme);
    } catch (e) {}
  }

  function applyTheme(theme) {
    root.setAttribute('data-theme', theme);
    toggleBtn.textContent = theme === 'dark' ? 'Light mode' : 'Dark mode';
  }

  applyTheme(getSavedTheme());

  toggleBtn.addEventListener('click', function () {
    var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    saveTheme(next);
    applyTheme(next);
  });

  var form = document.getElementById('apply-form');
  form.addEventListener('submit', function (e) {
    e.preventDefault();
  });
})();
