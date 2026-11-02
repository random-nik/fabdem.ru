(() => {
  "use strict";

  const body = document.body;
  const MAX_TILES = Number(body.dataset.maxTiles || 9);
  const MAX_UPLOAD_MB = Number(body.dataset.maxUploadMb || 10);

  const el = {
    map: document.getElementById("map"),
    mapHint: document.getElementById("map-hint"),
    toast: document.getElementById("toast"),
    selectionSummary: document.getElementById("selection-summary"),
    west: document.getElementById("west"),
    south: document.getElementById("south"),
    east: document.getElementById("east"),
    north: document.getElementById("north"),
    applyBbox: document.getElementById("apply-bbox"),
    clearSelection: document.getElementById("clear-selection"),
    toolbarClear: document.getElementById("toolbar-clear"),
    fileInput: document.getElementById("file-input"),
    dropzone: document.getElementById("dropzone"),
    maskToggle: document.getElementById("mask-toggle"),
    crsSelect: document.getElementById("crs-select"),
    filename: document.getElementById("filename"),
    areaValue: document.getElementById("area-value"),
    tilesValue: document.getElementById("tiles-value"),
    sizeValue: document.getElementById("size-value"),
    limitValue: document.getElementById("limit-value"),
    estimateWarning: document.getElementById("estimate-warning"),
    archives: document.getElementById("archives"),
    downloadButton: document.getElementById("download-button"),
    progressCard: document.getElementById("progress-card"),
    progressMessage: document.getElementById("progress-message"),
    progressPercent: document.getElementById("progress-percent"),
    progressBar: document.getElementById("progress-bar"),
    progressActions: document.getElementById("progress-actions"),
    tileCard: document.getElementById("tile-card"),
    tileName: document.getElementById("tile-name"),
    tileBounds: document.getElementById("tile-bounds"),
    tileFile: document.getElementById("tile-file"),
  };

  const state = {
    selection: null,
    selectionLayer: null,
    selectionSource: null,
    estimate: null,
    estimateTimer: null,
    estimateToken: 0,
    gridLayer: null,
    gridTimer: null,
    gridToken: 0,
    gridErrorShown: false,
    jobId: null,
    pollTimer: null,
    toastTimer: null,
    editMode: false,
  };

  const SELECTION_STYLE = {
    color: "#0f766e",
    weight: 2,
    opacity: 1,
    fillColor: "#14b8a6",
    fillOpacity: 0.12,
  };
  const GRID_STYLE = {
    color: "#b45309",
    weight: 0.7,
    opacity: 0.55,
    fillColor: "#f59e0b",
    fillOpacity: 0.025,
  };
  const GRID_HOVER_STYLE = {
    color: "#0f766e",
    weight: 1.4,
    opacity: 0.9,
    fillColor: "#14b8a6",
    fillOpacity: 0.1,
  };
  const GRID_SELECTED_STYLE = {
    color: "#0f766e",
    weight: 2.2,
    opacity: 1,
    fillColor: "#14b8a6",
    fillOpacity: 0.18,
  };

  const map = L.map("map", {
    zoomControl: true,
    attributionControl: true,
  }).setView([55.75, 37.62], 5);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a>',
  }).addTo(map);

  L.control.scale({ imperial: false, position: "bottomright" }).addTo(map);
  map.zoomControl.setPosition("topright");

  if (map.pm && typeof map.pm.setLang === "function") {
    map.pm.setLang("ru");
  }

  function formatCoord(value) {
    return Number(value)
      .toFixed(4)
      .replace(/\.?0+$/, "");
  }

  function formatArea(value) {
    if (!Number.isFinite(value)) return "—";
    if (value < 10) {
      return `${value.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} км²`;
    }
    return `${Math.round(value).toLocaleString("ru-RU")} км²`;
  }

  function formatBytes(value) {
    if (!Number.isFinite(value) || value <= 0) return "—";
    if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} КБ`;
    if (value < 1024 * 1024 * 1024) {
      return `${(value / 1024 / 1024).toLocaleString("ru-RU", {
        maximumFractionDigits: 1,
      })} МБ`;
    }
    return `${(value / 1024 / 1024 / 1024).toLocaleString("ru-RU", {
      maximumFractionDigits: 2,
    })} ГБ`;
  }

  function plural(value, one, few, many) {
    const mod10 = value % 10;
    const mod100 = value % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
  }

  function formatBounds(bounds) {
    if (!bounds) return "—";
    const [west, south, east, north] = bounds;
    const cross = west > east ? " · через 180°" : "";
    return `W ${formatCoord(west)}°, S ${formatCoord(south)}° — E ${formatCoord(east)}°, N ${formatCoord(north)}°${cross}`;
  }

  function showToast(message, isError = false) {
    el.toast.textContent = message;
    el.toast.classList.toggle("is-error", Boolean(isError));
    el.toast.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => {
      el.toast.hidden = true;
    }, 6500);
  }

  async function responseError(response) {
    try {
      const data = await response.json();
      if (data && data.detail) {
        if (Array.isArray(data.detail)) {
          return data.detail.map((item) => item.msg || String(item)).join("; ");
        }
        return String(data.detail);
      }
    } catch (_) {
      // ignore non-JSON errors
    }
    return `Ошибка сервера (${response.status})`;
  }

  function setHint(text) {
    el.mapHint.textContent = text;
  }

  function setToolActive(tool) {
    document.querySelectorAll("[data-tool]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.tool === tool);
    });
  }

  function cancelDrawing() {
    try {
      map.pm.disableDraw();
    } catch (_) {
      // nothing to disable
    }
  }

  function disableEditMode() {
    try {
      if (map.pm.globalEditModeEnabled()) map.pm.disableGlobalEditMode();
    } catch (_) {
      // ignore
    }
    state.editMode = false;
  }

  function isEditMode() {
    try {
      return Boolean(map.pm.globalEditModeEnabled());
    } catch (_) {
      return state.editMode;
    }
  }

  function bboxLayer(bounds) {
    const [west, south, east, north] = bounds;
    if (west <= east) {
      return L.rectangle(
        [
          [south, west],
          [north, east],
        ],
        SELECTION_STYLE
      );
    }
    return L.featureGroup([
      L.rectangle(
        [
          [south, west],
          [north, 180],
        ],
        SELECTION_STYLE
      ),
      L.rectangle(
        [
          [south, -180],
          [north, east],
        ],
        SELECTION_STYLE
      ),
    ]);
  }

  function geometryLayer(geometry) {
    return L.geoJSON(geometry, { style: () => SELECTION_STYLE });
  }

  function layerForSelection(selection) {
    if (selection.mode === "bbox") return bboxLayer(selection.bbox);
    if (selection.mode === "tile") return bboxLayer(selection.bounds);
    if (selection.mode === "polygon") return geometryLayer(selection.geometry);
    return null;
  }

  function boundsFromLayer(layer) {
    const bounds = layer.getBounds();
    return [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()];
  }

  function fillInputs(bounds) {
    if (!bounds) return;
    const [west, south, east, north] = bounds;
    el.west.value = String(Number(west.toFixed(6)));
    el.south.value = String(Number(south.toFixed(6)));
    el.east.value = String(Number(east.toFixed(6)));
    el.north.value = String(Number(north.toFixed(6)));
  }

  function clearInputs() {
    [el.west, el.south, el.east, el.north].forEach((input) => {
      input.value = "";
    });
  }

  function countPolygonVertices(geometry) {
    if (!geometry) return 0;
    if (geometry.type === "FeatureCollection") {
      return (geometry.features || []).reduce(
        (total, feature) => total + countPolygonVertices(feature.geometry),
        0
      );
    }
    if (geometry.type === "Feature") return countPolygonVertices(geometry.geometry);
    if (!geometry.coordinates) return 0;
    if (geometry.type === "Polygon") return Math.max(0, geometry.coordinates[0].length - 1);
    if (geometry.type === "MultiPolygon") {
      return geometry.coordinates.reduce(
        (total, polygon) => total + Math.max(0, polygon[0].length - 1),
        0
      );
    }
    return 0;
  }

  function updateSelectionSummary() {
    const selection = state.selection;
    if (!selection) {
      el.selectionSummary.textContent = "Область не выбрана";
      return;
    }
    if (selection.mode === "tile") {
      el.selectionSummary.textContent = `Плитка ${selection.tile}`;
    } else if (selection.mode === "polygon") {
      const count = countPolygonVertices(selection.geometry);
      el.selectionSummary.textContent = `Полигон · ${count.toLocaleString("ru-RU")} ${plural(count, "вершина", "вершины", "вершин")}`;
    } else {
      el.selectionSummary.textContent = formatBounds(selection.bounds);
    }
  }

  function updateTileCard() {
    const selection = state.selection;
    if (!selection || selection.mode !== "tile") {
      el.tileCard.hidden = true;
      return;
    }
    el.tileCard.hidden = false;
    el.tileName.textContent = selection.tile;
    el.tileBounds.textContent = formatBounds(selection.bounds);
    el.tileFile.textContent = selection.props?.file_name
      ? `Исходный файл: ${selection.props.file_name}`
      : "";
  }

  function refreshGridStyles() {
    if (state.gridLayer && typeof state.gridLayer.setStyle === "function") {
      state.gridLayer.setStyle(gridStyle);
    }
  }

  function setSelection(selection, layer = null, options = {}) {
    if (!selection) return;

    const actualLayer = layer || layerForSelection(selection);
    if (state.selectionLayer && state.selectionLayer !== actualLayer) {
      map.removeLayer(state.selectionLayer);
    }
    if (actualLayer && !map.hasLayer(actualLayer)) {
      actualLayer.addTo(map);
    }
    if (actualLayer && typeof actualLayer.setStyle === "function") {
      actualLayer.setStyle(SELECTION_STYLE);
    }
    if (actualLayer && actualLayer.eachLayer) {
      actualLayer.eachLayer((child) => {
        if (typeof child.setStyle === "function") child.setStyle(SELECTION_STYLE);
      });
    }

    if (!selection.bounds) {
      try {
        selection.bounds = boundsFromLayer(actualLayer);
      } catch (_) {
        selection.bounds = null;
      }
    }

    state.selection = selection;
    state.selectionLayer = actualLayer;
    fillInputs(selection.bounds);
    updateSelectionSummary();
    updateTileCard();
    refreshGridStyles();
    if (options.fit && actualLayer && typeof actualLayer.getBounds === "function") {
      map.fitBounds(actualLayer.getBounds(), { padding: [32, 32], maxZoom: 12 });
    }
    scheduleEstimate();
  }

  function clearProgress() {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
    state.jobId = null;
    el.progressCard.hidden = true;
    el.progressBar.style.width = "0%";
    el.progressBar.classList.remove("is-error");
    el.progressMessage.textContent = "Подготовка…";
    el.progressPercent.textContent = "0%";
    el.progressActions.replaceChildren();
  }

  function clearEstimate() {
    el.areaValue.textContent = "—";
    el.tilesValue.textContent = "—";
    el.sizeValue.textContent = "—";
    el.limitValue.textContent = `${MAX_TILES} плиток`;
    el.estimateWarning.hidden = true;
    el.estimateWarning.classList.remove("is-error");
    el.estimateWarning.textContent = "";
    el.archives.replaceChildren();
    el.downloadButton.disabled = true;
    el.downloadButton.textContent = "Скачать GeoTIFF";
  }

  function clearSelection(options = {}) {
    cancelDrawing();
    disableEditMode();
    if (state.selectionLayer) {
      map.removeLayer(state.selectionLayer);
      state.selectionLayer = null;
    }
    state.selection = null;
    state.selectionSource = null;
    state.estimate = null;
    state.estimateToken += 1;
    clearTimeout(state.estimateTimer);
    clearInputs();
    clearEstimate();
    clearProgress();
    updateSelectionSummary();
    updateTileCard();
    refreshGridStyles();
    el.fileInput.value = "";
    if (!options.keepTool) {
      state.mode = "tile";
      setToolActive("tile");
      setHint("Кликните по квадрату сетки, чтобы выбрать плитку 1°.");
    }
  }

  function scheduleEstimate() {
    clearTimeout(state.estimateTimer);
    state.estimateTimer = setTimeout(updateEstimate, 120);
  }

  function renderEstimateWarning(messages, isError = false) {
    if (!messages || messages.length === 0) {
      el.estimateWarning.hidden = true;
      el.estimateWarning.textContent = "";
      el.estimateWarning.classList.remove("is-error");
      return;
    }
    el.estimateWarning.hidden = false;
    el.estimateWarning.classList.toggle("is-error", isError);
    el.estimateWarning.textContent = messages.join("\n");
  }

  function renderArchives(archives) {
    el.archives.replaceChildren();
    if (!archives || archives.length === 0) return;
    const label = document.createElement("div");
    label.className = "small muted";
    label.textContent = "Исходные архивы 10°×10°:";
    el.archives.appendChild(label);
    archives.slice(0, 6).forEach((archive) => {
      const link = document.createElement("a");
      link.href = archive.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = archive.name;
      el.archives.appendChild(link);
    });
  }

  function renderEstimate(data) {
    state.estimate = data;
    el.areaValue.textContent = formatArea(data.area_km2);
    el.tilesValue.textContent = `${data.tile_count.toLocaleString("ru-RU")} / ${data.max_tiles}`;
    el.sizeValue.textContent = `~${formatBytes(data.estimated_bytes)}`;
    el.limitValue.textContent = `${data.max_tiles} плиток`;
    renderEstimateWarning(data.warnings || [], !data.within_limits);
    renderArchives(data.archives || []);
    el.downloadButton.disabled = !data.within_limits || !data.tile_count;
    el.downloadButton.textContent =
      data.mode === "tile" ? "Скачать плитку GeoTIFF" : "Скачать GeoTIFF";
  }

  function selectionPayload() {
    const common = {
      mask: Boolean(el.maskToggle.checked),
      crs: el.crsSelect.value,
      filename: el.filename.value.trim() || null,
    };
    const selection = state.selection;
    if (!selection) return null;
    if (selection.mode === "tile") {
      return { ...common, mode: "tile", tile: selection.tile };
    }
    if (selection.mode === "bbox") {
      return { ...common, mode: "bbox", bbox: selection.bbox };
    }
    return { ...common, mode: "polygon", geometry: selection.geometry };
  }

  async function updateEstimate() {
    const payload = selectionPayload();
    if (!payload) {
      clearEstimate();
      return;
    }

    const token = ++state.estimateToken;
    el.areaValue.textContent = "…";
    el.tilesValue.textContent = "…";
    el.sizeValue.textContent = "…";
    el.downloadButton.disabled = true;
    renderEstimateWarning([], false);
    el.archives.replaceChildren();

    try {
      const response = await fetch("/api/estimate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (token !== state.estimateToken) return;
      if (!response.ok) {
        throw new Error(await responseError(response));
      }
      const data = await response.json();
      if (token !== state.estimateToken) return;
      renderEstimate(data);
    } catch (error) {
      if (token !== state.estimateToken) return;
      state.estimate = null;
      el.areaValue.textContent = "—";
      el.tilesValue.textContent = "—";
      el.sizeValue.textContent = "—";
      renderEstimateWarning([error.message || "Не удалось оценить область"], true);
      el.archives.replaceChildren();
      el.downloadButton.disabled = true;
    }
  }

  function setProgress(percent, message) {
    const value = Math.max(0, Math.min(100, Number(percent) || 0));
    el.progressBar.style.width = `${value}%`;
    el.progressPercent.textContent = `${Math.round(value)}%`;
    el.progressMessage.textContent = message || "Подготовка…";
  }

  function stopPolling() {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  function renderJobDone(job) {
    stopPolling();
    setProgress(100, "Готово");
    el.progressBar.classList.remove("is-error");
    el.progressActions.replaceChildren();
    const link = document.createElement("a");
    link.href = job.download_url;
    link.download = job.filename || "";
    link.textContent = `Скачать ${job.filename || "файл"}`;
    el.progressActions.appendChild(link);
    el.downloadButton.disabled = false;
    showToast("Файл готов, скачивание начинается");
    // The click originates from the user's download action, so this is normally allowed.
    setTimeout(() => {
      const auto = document.createElement("a");
      auto.href = job.download_url;
      auto.download = job.filename || "";
      document.body.appendChild(auto);
      auto.click();
      auto.remove();
    }, 80);
  }

  function renderJobError(message) {
    stopPolling();
    el.progressBar.classList.add("is-error");
    setProgress(100, "Ошибка");
    el.progressActions.textContent = message || "Не удалось подготовить файл";
    el.downloadButton.disabled = false;
    showToast(message || "Не удалось подготовить файл", true);
  }

  async function pollJob(jobId) {
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
      if (!response.ok) {
        throw new Error(await responseError(response));
      }
      const job = await response.json();
      setProgress(job.progress || 0, job.message || "");
      if (job.status === "done") {
        renderJobDone(job);
        return;
      }
      if (job.status === "error") {
        renderJobError(job.error);
        return;
      }
      state.pollTimer = setTimeout(() => pollJob(jobId), 700);
    } catch (error) {
      renderJobError(error.message || "Не удалось получить статус задачи");
    }
  }

  async function startDownload() {
    const payload = selectionPayload();
    if (!payload || el.downloadButton.disabled) return;

    clearProgress();
    el.downloadButton.disabled = true;
    el.progressCard.hidden = false;
    el.progressBar.classList.remove("is-error");
    setProgress(0, "Создание задачи…");

    try {
      const response = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(await responseError(response));
      }
      const job = await response.json();
      state.jobId = job.id;
      setProgress(job.progress || 0, job.message || "В очереди");
      pollJob(job.id);
    } catch (error) {
      renderJobError(error.message || "Не удалось создать задачу");
    }
  }

  function gridStyle(feature) {
    const selected =
      state.selection &&
      state.selection.mode === "tile" &&
      state.selection.tile === feature?.properties?.tile_name;
    return selected ? GRID_SELECTED_STYLE : GRID_STYLE;
  }

  function selectTile(feature, layer) {
    const properties = feature.properties || {};
    const bounds = boundsFromLayer(layer);
    const selection = {
      mode: "tile",
      tile: properties.tile_name,
      bounds,
      props: properties,
    };
    const selectionLayer = bboxLayer(bounds);
    state.selectionSource = "tile";
    setSelection(selection, selectionLayer);
    setToolActive("tile");
    setHint("Плитка выбрана. Можно скачать оригинальный GeoTIFF.");
  }

  async function loadGrid() {
    if (map.getZoom() < 5) {
      if (state.gridLayer) {
        map.removeLayer(state.gridLayer);
        state.gridLayer = null;
      }
      setHint("Увеличьте масштаб до 5+, чтобы увидеть сетку плиток 1°.");
      return;
    }

    const token = ++state.gridToken;
    const mapBounds = map.getBounds();
    const bbox = [
      mapBounds.getWest(),
      mapBounds.getSouth(),
      mapBounds.getEast(),
      mapBounds.getNorth(),
    ].join(",");

    try {
      const response = await fetch(`/api/tiles.geojson?bbox=${encodeURIComponent(bbox)}`);
      if (token !== state.gridToken) return;
      if (!response.ok) throw new Error(await responseError(response));
      const data = await response.json();
      if (token !== state.gridToken) return;

      if (state.gridLayer) map.removeLayer(state.gridLayer);
      state.gridLayer = L.geoJSON(data, {
        style: gridStyle,
        onEachFeature(feature, layer) {
          layer.options.pmIgnore = true;
          layer.bindTooltip(feature.properties.tile_name, {
            sticky: true,
            direction: "top",
            opacity: 0.95,
          });
          layer.on("click", (event) => {
            L.DomEvent.stopPropagation(event);
            selectTile(feature, layer);
          });
          layer.on("mouseover", () => {
            const selected =
              state.selection &&
              state.selection.mode === "tile" &&
              state.selection.tile === feature.properties.tile_name;
            if (!selected) layer.setStyle(GRID_HOVER_STYLE);
          });
          layer.on("mouseout", () => layer.setStyle(gridStyle(feature)));
        },
      }).addTo(map);
      state.gridLayer.bringToBack();
      setHint(
        state.selection
          ? "Сетка 1° активна. Клик по квадрату заменит текущий выбор."
          : "Кликните по квадрату сетки, чтобы выбрать плитку 1°."
      );
    } catch (error) {
      if (token !== state.gridToken) return;
      if (!state.gridErrorShown) {
        state.gridErrorShown = true;
        showToast(error.message || "Не удалось загрузить сетку плиток", true);
      }
    }
  }

  function scheduleGrid() {
    clearTimeout(state.gridTimer);
    state.gridTimer = setTimeout(loadGrid, 250);
  }

  function selectViewport() {
    cancelDrawing();
    disableEditMode();
    const bounds = map.getBounds();
    const bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()];
    state.selectionSource = "viewport";
    setSelection({ mode: "bbox", bbox }, bboxLayer(bbox));
    setToolActive("viewport");
    setHint("Выбрана видимая область. Можно изменить границы вручную.");
  }

  function startTool(tool) {
    if (tool === "tile") {
      cancelDrawing();
      disableEditMode();
      clearSelection({ keepTool: true });
      state.mode = "tile";
      setToolActive("tile");
      setHint("Кликните по квадрату сетки, чтобы выбрать плитку 1°.");
      if (map.getZoom() < 5) {
        showToast("Увеличьте масштаб до 5+, чтобы увидеть сетку плиток");
      }
      return;
    }

    if (tool === "rect" || tool === "polygon") {
      clearSelection({ keepTool: true });
      cancelDrawing();
      disableEditMode();
      state.mode = tool;
      setToolActive(tool);
      const shape = tool === "rect" ? "Rectangle" : "Polygon";
      map.pm.enableDraw(shape, {
        snapDistance: 20,
        continueDrawing: false,
        allowSelfIntersection: false,
      });
      setHint(
        tool === "rect"
          ? "Нарисуйте прямоугольник на карте."
          : "Нарисуйте полигон. Завершите двойным кликом."
      );
      return;
    }

    if (tool === "viewport") {
      selectViewport();
      return;
    }

    if (tool === "edit") {
      if (!state.selection) {
        showToast("Сначала выберите область");
        return;
      }
      if (state.selection.mode === "tile") {
        showToast("Плитку нельзя редактировать. Нарисуйте прямоугольник или полигон.");
        return;
      }
      if (isEditMode()) {
        disableEditMode();
        setActiveToolForSelection();
      } else {
        try {
          map.pm.enableGlobalEditMode({ allowSelfIntersection: false });
          state.editMode = true;
          setToolActive("edit");
          setHint("Редактирование включено. Перетаскивайте вершины или рамку.");
        } catch (error) {
          showToast("Не удалось включить редактирование", true);
        }
      }
    }
  }

  function setActiveToolForSelection() {
    const selection = state.selection;
    if (!selection) {
      setToolActive("tile");
      return;
    }
    if (selection.mode === "tile") setToolActive("tile");
    else if (selection.mode === "polygon") setToolActive("polygon");
    else if (state.selectionSource === "viewport") setToolActive("viewport");
    else setToolActive("rect");
  }

  function applyBbox() {
    const values = [el.west, el.south, el.east, el.north].map((input) => Number(input.value));
    if (values.some((value) => !Number.isFinite(value))) {
      showToast("Введите все четыре координаты", true);
      return;
    }
    const [west, south, east, north] = values;
    if (south >= north) {
      showToast("Юг должен быть меньше севера", true);
      return;
    }
    if (west === east) {
      showToast("Запад и восток не должны совпадать", true);
      return;
    }
    if (Math.abs(west) > 180 || Math.abs(east) > 180 || Math.abs(south) > 90 || Math.abs(north) > 90) {
      showToast("Координаты вне допустимого диапазона", true);
      return;
    }
    cancelDrawing();
    disableEditMode();
    state.selectionSource = "bbox";
    setSelection({ mode: "bbox", bbox: [west, south, east, north] }, bboxLayer([west, south, east, north]));
    setToolActive("rect");
    setHint("Область задана координатами. Можно скачать GeoTIFF.");
  }

  async function handleFile(file) {
    if (!file) return;
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
      showToast(`Файл больше ${MAX_UPLOAD_MB} МБ`, true);
      return;
    }
    const formData = new FormData();
    formData.append("file", file);
    showToast("Читаю файл…");
    try {
      const response = await fetch("/api/parse-file", {
        method: "POST",
        body: formData,
      });
      if (!response.ok) throw new Error(await responseError(response));
      const data = await response.json();
      cancelDrawing();
      disableEditMode();
      state.selectionSource = "upload";
      const layer = geometryLayer(data.geometry);
      setSelection({ mode: "polygon", geometry: data.geometry }, layer, { fit: true });
      setToolActive("polygon");
      setHint("Полигон загружен. Можно скачать GeoTIFF.");
      showToast(`Загружено полигонов: ${data.feature_count}`);
    } catch (error) {
      showToast(error.message || "Не удалось прочитать файл", true);
    } finally {
      el.fileInput.value = "";
    }
  }

  function toggleEdit() {
    startTool("edit");
  }

  // --- Events -------------------------------------------------------------

  document.querySelectorAll("[data-tool]").forEach((button) => {
    button.addEventListener("click", () => {
      const tool = button.dataset.tool;
      if (tool === "edit") {
        toggleEdit();
      } else {
        startTool(tool);
      }
    });
  });

  el.clearSelection.addEventListener("click", () => clearSelection());
  el.toolbarClear.addEventListener("click", () => clearSelection());
  el.applyBbox.addEventListener("click", applyBbox);
  el.downloadButton.addEventListener("click", startDownload);
  el.maskToggle.addEventListener("change", scheduleEstimate);
  el.crsSelect.addEventListener("change", scheduleEstimate);
  el.filename.addEventListener("input", () => {
    // Filename does not affect the estimate; keep the button state as is.
  });

  el.fileInput.addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    handleFile(file);
  });

  ["dragenter", "dragover"].forEach((name) => {
    el.dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      el.dropzone.classList.add("is-dragover");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    el.dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      el.dropzone.classList.remove("is-dragover");
    });
  });
  el.dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    handleFile(file);
  });

  map.on("moveend", scheduleGrid);
  map.on("pm:create", (event) => {
    const layer = event.layer;
    if (!layer) return;
    const isRectangle = event.shape === "Rectangle" || layer instanceof L.Rectangle;
    if (isRectangle) {
      const bounds = layer.getBounds();
      const bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()];
      state.selectionSource = "rect";
      setSelection({ mode: "bbox", bbox }, layer);
      setToolActive("rect");
    } else {
      const geometry = layer.toGeoJSON().geometry;
      state.selectionSource = "polygon";
      setSelection({ mode: "polygon", geometry }, layer);
      setToolActive("polygon");
    }
    map.pm.disableDraw();
    setHint("Область выбрана. Можно скачать GeoTIFF.");
  });

  function isSelectionLayer(layer) {
    if (!state.selectionLayer) return false;
    if (layer === state.selectionLayer) return true;
    return (
      typeof state.selectionLayer.hasLayer === "function" &&
      state.selectionLayer.hasLayer(layer)
    );
  }

  map.on("pm:update", (event) => {
    if (!state.selection || !isSelectionLayer(event.layer)) return;
    if (state.selection.mode === "tile") return;
    if (state.selection.mode === "bbox" && typeof event.layer.getBounds === "function") {
      const bounds = event.layer.getBounds();
      state.selection.bbox = [
        bounds.getWest(),
        bounds.getSouth(),
        bounds.getEast(),
        bounds.getNorth(),
      ];
      state.selection.bounds = state.selection.bbox;
    } else if (state.selection.mode === "polygon") {
      const source = state.selectionLayer || event.layer;
      if (source && typeof source.toGeoJSON === "function") {
        const geojson = source.toGeoJSON();
        state.selection.geometry =
          geojson.type === "FeatureCollection" ? geojson : geojson.geometry;
      }
      try {
        state.selection.bounds = boundsFromLayer(source || event.layer);
      } catch (_) {
        // ignore
      }
    }
    fillInputs(state.selection.bounds);
    updateSelectionSummary();
    scheduleEstimate();
  });

  map.on("pm:remove", (event) => {
    if (isSelectionLayer(event.layer)) {
      clearSelection();
    }
  });

  map.on("pm:globaleditmodetoggled", (event) => {
    state.editMode = Boolean(event.enabled);
    if (state.editMode) setToolActive("edit");
    else setActiveToolForSelection();
  });

  window.addEventListener("resize", () => {
    map.invalidateSize();
  });

  // --- Initial state ------------------------------------------------------

  setToolActive("tile");
  setHint("Кликните по квадрату сетки, чтобы выбрать плитку 1°.");
  window.fabdemMap = map;
  setTimeout(loadGrid, 150);
})();
