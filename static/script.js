const searchInput = document.getElementById('searchInput');
const searchBtn = document.getElementById('searchBtn');
const resultsGrid = document.getElementById('resultsGrid');
const searchInfo = document.getElementById('searchInfo');
const resolvedEntityName = document.getElementById('resolvedEntityName');
const resolvedEntityDesc = document.getElementById('resolvedEntityDesc');
const loader = document.getElementById('loader');

const splitView = document.getElementById('splitView');
const closeSplitView = document.getElementById('closeSplitView');
const detailImage = document.getElementById('detailImage');
const detailTitle = document.getElementById('detailTitle');
const detailDesc = document.getElementById('detailDesc');
const detailLink = document.getElementById('detailLink');
const depictsList = document.getElementById('depictsList');

const addDepictInput = document.getElementById('addDepictInput');
const resolveDepictBtn = document.getElementById('resolveDepictBtn');
const autocompleteList = document.getElementById('autocompleteList');
const selectedDepictBase = document.getElementById('selectedDepictBase');
const selectedLabel = document.getElementById('selectedLabel');
const selectedId = document.getElementById('selectedId');
const selectedDesc = document.getElementById('selectedDesc');
const confirmAddBtn = document.getElementById('confirmAddBtn');
const actionAlert = document.getElementById('actionAlert');

let currentMediaId = null;
let currentSelectedQID = null;

// Search Function
let currentPage = 1;
let currentQuery = '';

async function performSearch(isLoadMore = false) {
    if (!isLoadMore) {
        currentQuery = searchInput.value.trim();
        currentPage = 1;
        resultsGrid.innerHTML = '';
        document.getElementById('loadMoreContainer').style.display = 'none';
        searchInfo.style.display = 'none';
    }

    if (!currentQuery) return;

    loader.style.display = 'block';

    if (isLoadMore) {
        document.getElementById('loadMoreBtn').disabled = true;
        document.getElementById('loadMoreBtn').innerText = 'Loading...';
    }

    try {
        const res = await fetch(`/api/search?q=${encodeURIComponent(currentQuery)}&page=${currentPage}`);
        const data = await res.json();

        loader.style.display = 'none';

        if (data.found_entity && !isLoadMore) {
            if (data.found_entity.id === data.found_entity.label) {
                resolvedEntityName.innerText = data.found_entity.label;
            } else {
                resolvedEntityName.innerText = `${data.found_entity.label} (${data.found_entity.id})`;
            }
            resolvedEntityDesc.innerText = data.found_entity.description;
            searchInfo.style.display = 'block';
        }

        if (data.results && data.results.length > 0) {
            data.results.forEach(file => {
                const item = document.createElement('div');
                item.className = 'grid-item';
                item.onclick = () => openSplitView(file);

                item.innerHTML = `
                    <img src="${file.thumb_url}" alt="${file.title}" loading="lazy">
                    <div class="grid-item-info">
                        <div class="grid-item-title" title="${file.title}">${file.title.replace("File:", "")}</div>
                        <div class="mt-2 d-flex flex-wrap gap-1">
                            ${file.depicts ? file.depicts.map(d => `<span class="badge bg-secondary" style="font-size: 0.7em;">${d.label}</span>`).join('') : ''}
                        </div>
                    </div>
                `;
                resultsGrid.appendChild(item);
            });

            // Handle Load More Button
            if (data.has_next) {
                document.getElementById('loadMoreContainer').style.display = 'block';
                document.getElementById('loadMoreBtn').disabled = false;
                document.getElementById('loadMoreBtn').innerText = 'Load More';
            } else {
                document.getElementById('loadMoreContainer').style.display = 'none';
            }

        } else {
            if (!isLoadMore) {
                resultsGrid.innerHTML = '<p class="text-center w-100">No images found depicting this entity.</p>';
            }
            document.getElementById('loadMoreContainer').style.display = 'none';
        }

    } catch (e) {
        console.error(e);
        loader.style.display = 'none';
        if (!isLoadMore) resultsGrid.innerHTML = '<p class="text-danger text-center w-100">Error searching.</p>';
    }
}

searchBtn.addEventListener('click', () => performSearch(false));
searchInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') performSearch(false);
});

document.getElementById('loadMoreBtn').addEventListener('click', () => {
    currentPage++;
    performSearch(true);
});

// Split View
async function openSplitView(file) {
    splitView.style.display = 'flex';
    detailImage.src = file.url; // Use full size or large thumb
    detailTitle.innerText = file.title.replace("File:", "");
    detailDesc.innerHTML = file.description;
    detailLink.href = "https://commons.wikimedia.org/wiki/" + encodeURIComponent(file.title); // Actually this is the image url, we should link to description page: "https://commons.wikimedia.org/wiki/" + file.title
    detailLink.href = "https://commons.wikimedia.org/wiki/" + encodeURIComponent(file.title);

    // Reset Right Panel
    depictsList.innerHTML = '<div class="spinner-border spinner-border-sm text-primary" role="status"></div> Loading...';
    actionAlert.className = 'alert d-none';
    resetAddForm();

    // Fetch Details to get M-ID and Depicts
    try {
        const res = await fetch(`/api/file/${file.pageid}`);
        const data = await res.json();

        currentMediaId = data.mid;

        renderDepicts(data.depicts);

    } catch (e) {
        depictsList.innerHTML = '<span class="text-danger">Failed to load depicts data.</span>';
    }
}

function renderDepicts(depictsArray) {
    depictsList.innerHTML = '';
    if (!depictsArray || depictsArray.length === 0) {
        depictsList.innerHTML = '<em class="text-muted">No depicts statements found.</em>';
        return;
    }
    depictsArray.forEach(d => {
        const badge = document.createElement('span');
        badge.className = 'badge bg-secondary rounded-pill fw-normal p-2';
        badge.innerText = `${d.label} (${d.id})`;
        depictsList.appendChild(badge);
    });
}

closeSplitView.addEventListener('click', () => {
    splitView.style.display = 'none';
    detailImage.src = '';
});

// Add Depicts Logic
let searchTimeout = null;

addDepictInput.addEventListener('input', () => {
    const val = addDepictInput.value;
    clearTimeout(searchTimeout);
    if (!val) {
        autocompleteList.innerHTML = '';
        return;
    }
    searchTimeout = setTimeout(() => {
        fetchWikidataAutocomplete(val);
    }, 400);
});

async function fetchWikidataAutocomplete(query) {
    try {
        const res = await fetch(`/api/wikidata_search?q=${encodeURIComponent(query)}`);
        const items = await res.json();

        autocompleteList.innerHTML = '';
        items.forEach(item => {
            const div = document.createElement('div');
            div.className = 'autocomplete-item';
            div.innerHTML = `<strong>${item.label}</strong> <small>(${item.id})</small><br><span class="small text-muted">${item.description}</span>`;
            div.onclick = () => selectDepictCandidate(item);
            autocompleteList.appendChild(div);
        });
    } catch (e) {
        console.error(e);
    }
}

function selectDepictCandidate(item) {
    currentSelectedQID = item.id;
    selectedLabel.innerText = item.label;
    selectedId.innerText = item.id;
    selectedDesc.innerText = item.description;

    selectedDepictBase.style.display = 'block';
    autocompleteList.innerHTML = '';
    addDepictInput.value = '';
}

function resetAddForm() {
    selectedDepictBase.style.display = 'none';
    currentSelectedQID = null;
    autocompleteList.innerHTML = '';
    addDepictInput.value = '';
}

confirmAddBtn.addEventListener('click', async () => {
    if (!currentMediaId || !currentSelectedQID) return;

    confirmAddBtn.disabled = true;
    confirmAddBtn.innerText = 'Adding...';

    try {
        const res = await fetch('/api/add_claim', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                mid: currentMediaId,
                qid: currentSelectedQID
            })
        });

        const data = await res.json();

        if (data.success) {
            actionAlert.className = 'alert alert-success mt-2';
            actionAlert.innerText = 'Successfully added depicts statement!';
            actionAlert.classList.remove('d-none');

            // Refresh the depicts list
            const refreshRes = await fetch(`/api/file/${currentMediaId.replace("M", "")}`); // Hacky ID passing, ideally api/file should accept mid or we store pageid
            // Wait, api/file takes pageid. Mid corresponds to pageid. M123 -> 123
            // So we need to recover pageid from currentMediaId (M...)
            const pageIdRaw = currentMediaId.replace("M", "");

            const refreshData = await (await fetch(`/api/file/${pageIdRaw}`)).json();
            renderDepicts(refreshData.depicts);

            resetAddForm();
        } else {
            throw new Error(data.error || 'Unknown error');
        }

    } catch (e) {
        actionAlert.className = 'alert alert-danger mt-2';
        actionAlert.innerText = 'Error adding statement: ' + e.message;
        actionAlert.classList.remove('d-none');
    } finally {
        confirmAddBtn.disabled = false;
        confirmAddBtn.innerText = 'Add';
    }
});
