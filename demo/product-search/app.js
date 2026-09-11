/*
 * Demo store behaviour.
 *
 * Search and filters update the URL and re-render in place, without a page
 * load. That is deliberate: it exercises SurfAI's MutationObserver-based change
 * detection rather than letting it cheat by watching for navigations.
 */

(function () {
  'use strict';

  const PAGE_SIZE = 6;

  const state = { query: '', maxPrice: '', category: '', brand: '', inStock: false, page: 1 };

  const grid = document.getElementById('product-grid');
  const count = document.getElementById('result-count');
  const pageIndicator = document.getElementById('page-indicator');
  const prevButton = document.getElementById('prev-page');
  const nextButton = document.getElementById('next-page');

  function readStateFromUrl() {
    const params = new URLSearchParams(window.location.search);
    state.query = params.get('q') || '';
    state.maxPrice = params.get('max_price') || '';
    state.category = params.get('category') || '';
    state.brand = params.get('brand') || '';
    state.inStock = params.get('in_stock') === '1';
    state.page = Math.max(1, Number(params.get('page') || 1));

    document.getElementById('search-input').value = state.query;
    document.getElementById('max-price').value = state.maxPrice;
    document.getElementById('category').value = state.category;
    document.getElementById('brand').value = state.brand;
    document.getElementById('in-stock').checked = state.inStock;
  }

  function writeStateToUrl() {
    const params = new URLSearchParams();
    if (state.query) params.set('q', state.query);
    if (state.maxPrice) params.set('max_price', state.maxPrice);
    if (state.category) params.set('category', state.category);
    if (state.brand) params.set('brand', state.brand);
    if (state.inStock) params.set('in_stock', '1');
    if (state.page > 1) params.set('page', String(state.page));

    const search = params.toString();
    const url = search ? `${window.location.pathname}?${search}` : window.location.pathname;
    window.history.replaceState({}, '', url);
  }

  function filtered() {
    const query = state.query.trim().toLowerCase();
    const terms = query ? query.split(/\s+/) : [];

    return window.DEMO_PRODUCTS.filter(function (product) {
      const haystack = [product.name, product.brand, product.gpu, product.category]
        .join(' ')
        .toLowerCase();

      if (terms.length && !terms.every((term) => haystack.includes(term))) return false;
      if (state.maxPrice && product.price > Number(state.maxPrice)) return false;
      if (state.category && product.category !== state.category) return false;
      if (state.brand && product.brand !== state.brand) return false;
      if (state.inStock && !product.stock) return false;
      return true;
    });
  }

  function formatPrice(value) {
    return '₹' + value.toLocaleString('en-IN');
  }

  function render() {
    const matches = filtered();
    const totalPages = Math.max(1, Math.ceil(matches.length / PAGE_SIZE));
    if (state.page > totalPages) state.page = totalPages;

    const start = (state.page - 1) * PAGE_SIZE;
    const visible = matches.slice(start, start + PAGE_SIZE);

    grid.textContent = '';

    if (visible.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'no-results';
      empty.textContent = 'No products match those filters. Try widening your search.';
      grid.appendChild(empty);
    }

    visible.forEach(function (product) {
      const card = document.createElement('article');
      card.className = 'product-card';

      const title = document.createElement('h3');
      title.className = 'product-name';
      title.textContent = product.name;

      const price = document.createElement('p');
      price.className = 'product-price';
      price.textContent = formatPrice(product.price);

      const specs = document.createElement('p');
      specs.className = 'product-specs';
      specs.textContent = [product.brand, product.gpu, product.ram]
        .filter(Boolean)
        .join(' · ');

      const availability = document.createElement('p');
      availability.className = product.stock ? 'in-stock' : 'out-of-stock';
      availability.textContent = product.stock ? 'In stock' : 'Out of stock';

      const link = document.createElement('a');
      link.className = 'product-link';
      link.href = 'product.html?id=' + product.id;
      link.textContent = 'View product';

      const buy = document.createElement('button');
      buy.type = 'button';
      buy.className = 'buy-button';
      buy.textContent = 'Buy now';
      // Present so the risk classifier has something high-risk to catch. It
      // never charges anything -- this is a static demo.
      buy.addEventListener('click', function () {
        window.alert('This is a demo store. Nothing was ordered.');
      });

      card.append(title, price, specs, availability, link, buy);
      grid.appendChild(card);
    });

    count.textContent =
      matches.length === 1 ? 'Showing 1 product' : 'Showing ' + matches.length + ' products';
    pageIndicator.textContent = 'Page ' + state.page + ' of ' + totalPages;
    prevButton.disabled = state.page <= 1;
    nextButton.disabled = state.page >= totalPages;

    writeStateToUrl();
  }

  document.getElementById('search-form').addEventListener('submit', function (event) {
    event.preventDefault();
    state.query = document.getElementById('search-input').value;
    state.page = 1;
    render();
  });

  document.getElementById('apply-filters').addEventListener('click', function () {
    state.maxPrice = document.getElementById('max-price').value;
    state.category = document.getElementById('category').value;
    state.brand = document.getElementById('brand').value;
    state.inStock = document.getElementById('in-stock').checked;
    state.page = 1;
    render();
  });

  document.getElementById('clear-filters').addEventListener('click', function () {
    state.maxPrice = '';
    state.category = '';
    state.brand = '';
    state.inStock = false;
    state.page = 1;
    readStateFromUrl();
    document.getElementById('max-price').value = '';
    document.getElementById('category').value = '';
    document.getElementById('brand').value = '';
    document.getElementById('in-stock').checked = false;
    render();
  });

  // Changing a dropdown applies immediately: the common pattern on real stores.
  ['max-price', 'category', 'brand'].forEach(function (id) {
    document.getElementById(id).addEventListener('change', function () {
      document.getElementById('apply-filters').click();
    });
  });

  prevButton.addEventListener('click', function () {
    if (state.page > 1) {
      state.page -= 1;
      render();
    }
  });

  nextButton.addEventListener('click', function () {
    state.page += 1;
    render();
  });

  document.getElementById('signin').addEventListener('click', function () {
    window.location.href = 'signin.html';
  });

  readStateFromUrl();
  render();
})();
