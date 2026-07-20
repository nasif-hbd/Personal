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

  // Set this to your deployed Cloud Function URL (see functions/submitApplication).
  var SUBMIT_ENDPOINT = 'https://REGION-PROJECT_ID.cloudfunctions.net/submitApplication';

  var form = document.getElementById('apply-form');
  var submitBtn = document.getElementById('apply-submit');
  var status = document.getElementById('apply-status');

  form.addEventListener('submit', function (e) {
    e.preventDefault();

    var data = {
      name: form.name.value.trim(),
      age: form.age.value.trim(),
      email: form.email.value.trim(),
      phone: form.phone.value.trim(),
      program: form.program.value.trim(),
      reason: form.reason.value.trim(),
    };

    if (!data.name || !data.email) {
      status.textContent = 'Please fill in at least your name and email.';
      status.setAttribute('data-state', 'error');
      return;
    }

    submitBtn.disabled = true;
    status.removeAttribute('data-state');
    status.textContent = 'Submitting…';

    fetch(SUBMIT_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
      .then(function (res) {
        if (!res.ok) return res.json().then(function (body) { throw new Error(body.error || 'Submission failed.'); });
        return res.json();
      })
      .then(function () {
        status.textContent = 'Thanks! Your application was submitted — check your email for confirmation.';
        form.reset();
      })
      .catch(function (err) {
        status.textContent = err.message || 'Something went wrong. Please try again.';
        status.setAttribute('data-state', 'error');
      })
      .finally(function () {
        submitBtn.disabled = false;
      });
  });
})();
