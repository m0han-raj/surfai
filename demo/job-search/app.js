/*
 * Demo job board behaviour.
 *
 * Mirrors the store's in-place rendering, and adds an "Apply now" button so the
 * risk classifier has a medium-risk submit to catch.
 */

(function () {
  'use strict';

  const PAGE_SIZE = 5;
  const state = { query: '', location: '', experience: '', type: '', page: 1 };

  const list = document.getElementById('job-list');
  const count = document.getElementById('job-count');
  const pageLabel = document.getElementById('job-page');
  const prev = document.getElementById('job-prev');
  const next = document.getElementById('job-next');

  function readStateFromUrl() {
    const params = new URLSearchParams(window.location.search);
    state.query = params.get('q') || '';
    state.location = params.get('location') || '';
    state.experience = params.get('experience') || '';
    state.type = params.get('type') || '';
    state.page = Math.max(1, Number(params.get('page') || 1));

    document.getElementById('job-query').value = state.query;
    document.getElementById('location').value = state.location;
    document.getElementById('experience').value = state.experience;
    document.getElementById('job-type').value = state.type;
  }

  function writeStateToUrl() {
    const params = new URLSearchParams();
    if (state.query) params.set('q', state.query);
    if (state.location) params.set('location', state.location);
    if (state.experience) params.set('experience', state.experience);
    if (state.type) params.set('type', state.type);
    if (state.page > 1) params.set('page', String(state.page));

    const search = params.toString();
    window.history.replaceState(
      {},
      '',
      search ? window.location.pathname + '?' + search : window.location.pathname,
    );
  }

  function filtered() {
    const query = state.query.trim().toLowerCase();
    const terms = query ? query.split(/\s+/) : [];

    return window.DEMO_JOBS.filter(function (job) {
      const haystack = [job.title, job.company, job.skills.join(' ')].join(' ').toLowerCase();
      if (terms.length && !terms.every((term) => haystack.includes(term))) return false;
      if (state.location && job.location !== state.location) return false;
      if (state.experience && job.experience !== state.experience) return false;
      if (state.type && job.type !== state.type) return false;
      return true;
    });
  }

  function render() {
    const matches = filtered();
    const totalPages = Math.max(1, Math.ceil(matches.length / PAGE_SIZE));
    if (state.page > totalPages) state.page = totalPages;

    const start = (state.page - 1) * PAGE_SIZE;
    const visible = matches.slice(start, start + PAGE_SIZE);

    list.textContent = '';

    if (visible.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'no-results';
      empty.textContent = 'No jobs match those filters.';
      list.appendChild(empty);
    }

    visible.forEach(function (job) {
      const card = document.createElement('article');
      card.className = 'job-card';

      const title = document.createElement('h3');
      title.className = 'job-title';
      title.textContent = job.title;

      const meta = document.createElement('p');
      meta.className = 'job-meta';
      meta.textContent = [
        job.company,
        job.location,
        job.experience + ' years',
        job.type,
      ].join(' · ');

      const skills = document.createElement('p');
      skills.className = 'job-meta';
      skills.textContent = 'Skills: ' + job.skills.join(', ');

      const link = document.createElement('a');
      link.className = 'job-link';
      link.href = 'job.html?id=' + job.id;
      link.textContent = 'View job';

      const apply = document.createElement('button');
      apply.type = 'button';
      apply.className = 'apply-button';
      apply.textContent = 'Apply now';
      apply.addEventListener('click', function () {
        window.alert('This is a demo job board. No application was submitted.');
      });

      card.append(title, meta, skills, link, apply);
      list.appendChild(card);
    });

    count.textContent =
      matches.length === 1 ? 'Showing 1 job' : 'Showing ' + matches.length + ' jobs';
    pageLabel.textContent = 'Page ' + state.page + ' of ' + totalPages;
    prev.disabled = state.page <= 1;
    next.disabled = state.page >= totalPages;

    writeStateToUrl();
  }

  document.getElementById('job-form').addEventListener('submit', function (event) {
    event.preventDefault();
    state.query = document.getElementById('job-query').value;
    state.page = 1;
    render();
  });

  document.getElementById('apply-job-filters').addEventListener('click', function () {
    state.location = document.getElementById('location').value;
    state.experience = document.getElementById('experience').value;
    state.type = document.getElementById('job-type').value;
    state.page = 1;
    render();
  });

  ['location', 'experience', 'job-type'].forEach(function (id) {
    document.getElementById(id).addEventListener('change', function () {
      document.getElementById('apply-job-filters').click();
    });
  });

  prev.addEventListener('click', function () {
    if (state.page > 1) {
      state.page -= 1;
      render();
    }
  });

  next.addEventListener('click', function () {
    state.page += 1;
    render();
  });

  readStateFromUrl();
  render();
})();
