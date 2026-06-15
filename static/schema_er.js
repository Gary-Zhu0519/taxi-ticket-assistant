(function () {
    const board = document.getElementById("er-board");
    if (!board) return;

    const entities = JSON.parse(board.dataset.entities || "[]");
    const relations = JSON.parse(board.dataset.relations || "[]");
    const svg = document.getElementById("er-stage");
    const mobileList = document.getElementById("er-mobile-list");
    const detailTitle = document.getElementById("er-detail-title");
    const detailDescription = document.getElementById("er-detail-description");
    const detailKeys = document.getElementById("er-detail-keys");
    const detailFields = document.getElementById("er-detail-fields");
    const detailRelations = document.getElementById("er-detail-relations");

    const entityMap = new Map(entities.map((item) => [item.id, item]));
    const relationMap = new Map(relations.map((item) => [item.id, item]));
    const entityButtons = new Map();
    const relationButtons = new Map();

    const boxWidth = 190;
    const boxHeight = 88;

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function setDetailForEntity(entityId) {
        const entity = entityMap.get(entityId);
        if (!entity) return;

        detailTitle.textContent = `${entity.title} · ${entity.label}`;
        detailDescription.textContent = entity.description;
        const fkText = entity.fks.length ? `；外键：${entity.fks.join("，")}` : "；外键：无";
        detailKeys.textContent = `主键：${entity.pk}${fkText}`;
        detailFields.innerHTML = entity.fields.map((field) => `<span class="er-chip">${escapeHtml(field)}</span>`).join("");

        const related = relations.filter((rel) => rel.from === entityId || rel.to === entityId);
        detailRelations.innerHTML = related
            .map((rel) => `<button type="button" class="er-chip er-chip-button" data-relation-target="${rel.id}">${escapeHtml(rel.summary)} · ${escapeHtml(rel.cardinality)}</button>`)
            .join("");

        entityButtons.forEach((button, key) => button.classList.toggle("is-active", key === entityId));
        relationButtons.forEach((button, key) => {
            const rel = relationMap.get(key);
            const active = rel && (rel.from === entityId || rel.to === entityId);
            button.classList.toggle("is-related", Boolean(active));
            button.classList.toggle("is-active", false);
        });
    }

    function setDetailForRelation(relationId) {
        const relation = relationMap.get(relationId);
        if (!relation) return;

        detailTitle.textContent = relation.summary;
        detailDescription.textContent = relation.description;
        detailKeys.textContent = `基数：${relation.cardinality}；起点：${relation.from}；终点：${relation.to}`;
        detailFields.innerHTML = `
            <span class="er-chip">${escapeHtml(relation.cardinality)}</span>
            <span class="er-chip">${escapeHtml(entityMap.get(relation.from)?.title || relation.from)}</span>
            <span class="er-chip">${escapeHtml(entityMap.get(relation.to)?.title || relation.to)}</span>
        `;
        detailRelations.innerHTML = `
            <button type="button" class="er-chip er-chip-button" data-entity-target="${relation.from}">
                查看 ${escapeHtml(entityMap.get(relation.from)?.title || relation.from)}
            </button>
            <button type="button" class="er-chip er-chip-button" data-entity-target="${relation.to}">
                查看 ${escapeHtml(entityMap.get(relation.to)?.title || relation.to)}
            </button>
        `;

        entityButtons.forEach((button) => button.classList.toggle("is-active", false));
        relationButtons.forEach((button, key) => {
            button.classList.toggle("is-active", key === relationId);
            button.classList.toggle("is-related", false);
        });
    }

    function addSvgLine(relation) {
        const from = entityMap.get(relation.from);
        const to = entityMap.get(relation.to);
        if (!from || !to) return;

        const x1 = from.x + boxWidth;
        const y1 = from.y + boxHeight / 2;
        const x2 = to.x;
        const y2 = to.y + boxHeight / 2;
        const midX = (x1 + x2) / 2;
        const pathData = `M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`;

        const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
        group.setAttribute("class", "er-relation-group");
        group.dataset.relationId = relation.id;

        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", pathData);
        path.setAttribute("class", "er-relation-path");

        const hit = document.createElementNS("http://www.w3.org/2000/svg", "path");
        hit.setAttribute("d", pathData);
        hit.setAttribute("class", "er-relation-hit");

        const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
        label.setAttribute("x", String(midX));
        label.setAttribute("y", String((y1 + y2) / 2 - 8));
        label.setAttribute("text-anchor", "middle");
        label.setAttribute("class", "er-relation-label");
        label.textContent = relation.cardinality;

        group.appendChild(path);
        group.appendChild(hit);
        group.appendChild(label);
        svg.appendChild(group);
        relationButtons.set(relation.id, group);

        group.addEventListener("click", () => setDetailForRelation(relation.id));
    }

    function addSvgEntity(entity) {
        const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
        group.setAttribute("class", "er-entity-group");
        group.dataset.entityId = entity.id;
        group.dataset.group = entity.group || "foundation";
        group.setAttribute("transform", `translate(${entity.x}, ${entity.y})`);

        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("width", String(boxWidth));
        rect.setAttribute("height", String(boxHeight));
        rect.setAttribute("rx", "18");
        rect.setAttribute("class", "er-entity-box");

        const title = document.createElementNS("http://www.w3.org/2000/svg", "text");
        title.setAttribute("x", "18");
        title.setAttribute("y", "30");
        title.setAttribute("class", "er-entity-title");
        title.textContent = entity.title;

        const subtitle = document.createElementNS("http://www.w3.org/2000/svg", "text");
        subtitle.setAttribute("x", "18");
        subtitle.setAttribute("y", "54");
        subtitle.setAttribute("class", "er-entity-subtitle");
        subtitle.textContent = entity.label;

        const pk = document.createElementNS("http://www.w3.org/2000/svg", "text");
        pk.setAttribute("x", "18");
        pk.setAttribute("y", "73");
        pk.setAttribute("class", "er-entity-pk");
        pk.textContent = `PK: ${entity.pk}`;

        group.appendChild(rect);
        group.appendChild(title);
        group.appendChild(subtitle);
        group.appendChild(pk);
        svg.appendChild(group);
        entityButtons.set(entity.id, group);

        group.addEventListener("click", () => setDetailForEntity(entity.id));
    }

    function renderMobileCards() {
        mobileList.innerHTML = entities
            .map((entity) => {
                const relationCount = relations.filter((rel) => rel.from === entity.id || rel.to === entity.id).length;
                return `
                    <button type="button" class="er-mobile-card" data-mobile-entity="${entity.id}">
                        <div class="fw-semibold">${escapeHtml(entity.title)}</div>
                        <div class="small text-secondary">${escapeHtml(entity.label)}</div>
                        <div class="small text-secondary mt-2">关系数：${relationCount}</div>
                    </button>
                `;
            })
            .join("");
    }

    relations.forEach(addSvgLine);
    entities.forEach(addSvgEntity);
    renderMobileCards();
    setDetailForEntity("ticket");

    board.addEventListener("click", (event) => {
        const relationButton = event.target.closest("[data-relation-target]");
        if (relationButton) {
            setDetailForRelation(relationButton.dataset.relationTarget);
        }
        const entityButton = event.target.closest("[data-entity-target]");
        if (entityButton) {
            setDetailForEntity(entityButton.dataset.entityTarget);
        }
    });

    mobileList.addEventListener("click", (event) => {
        const card = event.target.closest("[data-mobile-entity]");
        if (!card) return;
        setDetailForEntity(card.dataset.mobileEntity);
        document.getElementById("er-detail-card")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
})();
