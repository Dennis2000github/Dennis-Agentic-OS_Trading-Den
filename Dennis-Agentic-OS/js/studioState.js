// Studio state helpers. Pure, dependency-free and node-testable.
// Shared by js/studio.js (generation console) and tests/studioState.test.js.
(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.StudioState = api;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  var STATE_LABELS = {
    queued: "Queued",
    running: "Generating",
    succeeded: "Ready",
    failed: "Failed",
    cancelled: "Cancelled"
  };

  var STATE_TONES = {
    queued: "queued",
    running: "running",
    succeeded: "ok",
    failed: "error",
    cancelled: "muted"
  };

  function elapsedLabel(seconds) {
    var total = Math.max(0, Math.floor(Number(seconds) || 0));
    if (total < 60) return total + "s";
    var minutes = Math.floor(total / 60);
    var rest = total % 60;
    if (minutes < 60) return minutes + "m " + String(rest).padStart(2, "0") + "s";
    var hours = Math.floor(minutes / 60);
    return hours + "h " + String(minutes % 60).padStart(2, "0") + "m";
  }

  function clockLabel(seconds) {
    var total = Math.max(0, Math.floor(Number(seconds) || 0));
    var minutes = Math.floor(total / 60);
    var rest = total % 60;
    return String(minutes).padStart(2, "0") + ":" + String(rest).padStart(2, "0");
  }

  function humanBytes(bytes) {
    var value = Number(bytes) || 0;
    if (value < 1024) return value + " B";
    var units = ["KB", "MB", "GB"];
    var unit = -1;
    do {
      value = value / 1024;
      unit += 1;
    } while (value >= 1024 && unit < units.length - 1);
    return (value >= 10 ? value.toFixed(0) : value.toFixed(1)) + " " + units[unit];
  }

  function stateLabel(state) {
    return STATE_LABELS[state] || "Unknown";
  }

  function stateTone(state) {
    return STATE_TONES[state] || "muted";
  }

  function isActive(job) {
    return !!job && (job.state === "queued" || job.state === "running");
  }

  function jobTitle(job) {
    if (!job) return "Job";
    return job.model_label || job.model_id || "Job";
  }

  function jobSummary(job) {
    if (!job) return "";
    var parts = [stateLabel(job.state)];
    if (job.elapsed_seconds != null) parts.push(elapsedLabel(job.elapsed_seconds));
    if (job.kind) parts.push(job.kind);
    return parts.join(" · ");
  }

  function jobErrorText(job) {
    if (!job || !job.error) return "";
    var message = job.error.message || job.error.code || "Generation failed";
    return String(message);
  }

  function normalizeList(payload, key) {
    if (Array.isArray(payload)) return payload;
    if (!payload || !Array.isArray(payload[key])) return [];
    return payload[key];
  }

  function modelHaystack(model) {
    var summary = (model && model.summary) || {};
    return [
      model && model.label,
      model && model.id,
      model && model.mode_label,
      model && model.mode,
      model && model.kind,
      (model && model.tags || []).join(" "),
      (summary.durations || []).join(" ")
    ].join(" ").toLowerCase();
  }

  function filterModels(models, kind, mode, query) {
    var terms = String(query || "").toLowerCase().trim().split(/\s+/).filter(Boolean);
    return (models || []).filter(function (model) {
      if (kind && model.kind !== kind) return false;
      if (mode && model.mode !== mode) return false;
      if (!terms.length) return true;
      var hay = modelHaystack(model);
      return terms.every(function (term) { return hay.indexOf(term) !== -1; });
    });
  }

  var MODE_INPUT = {
    text_to_image: "text",
    text_to_video: "text",
    image_edit: "image",
    image_to_video: "image",
    reference_to_video: "image",
    first_last_frame: "image",
    character_training: "image",
    video_edit: "video",
    video_extend: "video",
    video_reference: "video",
    motion_transfer: "video",
    object_swap: "video",
    restyle: "video"
  };

  function inputModeForModel(model) {
    if (!model) return "text";
    if (MODE_INPUT[model.mode]) return MODE_INPUT[model.mode];
    var accepts = (model.summary && model.summary.accepts) || {};
    if (accepts.video) return "video";
    if (accepts.image) return "image";
    return "text";
  }

  function filterModelsByInput(models, inputMode, kind, query) {
    var base = filterModels(models, kind, null, query);
    if (!inputMode) return base;
    return base.filter(function (model) {
      return inputModeForModel(model) === inputMode;
    });
  }

  function valuesFromParams(fields, params) {
    var values = defaultValues(fields);
    var ignored = [];
    Object.keys(params || {}).forEach(function (name) {
      var field = null;
      for (var i = 0; i < (fields || []).length; i += 1) {
        if (fields[i] && fields[i].name === name) { field = fields[i]; break; }
      }
      if (!field) { ignored.push(name); return; }
      var value = params[name];
      if (field.control === "json") {
        values[name] = typeof value === "string" ? value : JSON.stringify(value, null, 2);
      } else if (field.control === "url_list" && Array.isArray(value)) {
        values[name] = value.join("\n");
      } else if (field.control === "toggle") {
        values[name] = !!value;
      } else if (value === undefined || value === null) {
        values[name] = "";
      } else {
        values[name] = String(value);
      }
    });
    return { values: values, ignored: ignored };
  }

  function jobOutput(job) {
    var outputs = (job && job.outputs) || [];
    for (var i = 0; i < outputs.length; i += 1) {
      if (outputs[i] && (outputs[i].media_id || outputs[i].url)) return outputs[i];
    }
    return null;
  }

  function startOfToday(nowMs) {
    var date = new Date(nowMs == null ? Date.now() : nowMs);
    date.setHours(0, 0, 0, 0);
    return date.getTime() / 1000;
  }

  function countJobsSince(jobs, sinceEpoch) {
    return (jobs || []).filter(function (job) {
      return Number(job && job.created_at || 0) >= Number(sinceEpoch || 0);
    }).length;
  }

  function timestampLabel(epochSeconds) {
    var value = Number(epochSeconds);
    if (!isFinite(value) || value <= 0) return "";
    var date = new Date(value * 1000);
    var now = new Date();
    var sameDay = date.toDateString() === now.toDateString();
    try {
      if (sameDay) {
        return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      }
      return date.toLocaleDateString([], { month: "short", day: "numeric" }) + " " +
        date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (err) {
      return date.toISOString().slice(0, 16).replace("T", " ");
    }
  }

  function findById(items, id) {
    for (var i = 0; i < (items || []).length; i += 1) {
      if (items[i] && items[i].id === id) return items[i];
    }
    return null;
  }

  function fieldDefault(field) {
    if (!field) return "";
    if (field.default !== undefined && field.default !== null) return field.default;
    if (field.control === "select" && Array.isArray(field.enum) && field.enum.length) {
      return field.enum[0];
    }
    if (field.control === "toggle") return false;
    return "";
  }

  function defaultValues(fields) {
    var values = {};
    (fields || []).forEach(function (field) {
      if (!field || !field.name) return;
      values[field.name] = fieldDefault(field);
    });
    return values;
  }

  function coerceValue(field, raw) {
    var control = field && field.control;
    if (control === "toggle") {
      return raw === true || raw === "true" || raw === "on" || raw === 1;
    }
    if (raw === undefined || raw === null) return undefined;
    if (typeof raw === "string" && raw.trim() === "" && control !== "text") return undefined;

    if (control === "number") {
      var number = Number(raw);
      if (!isFinite(number)) {
        throw new Error((field.title || field.name) + " must be a number");
      }
      if (field.type === "integer") return Math.round(number);
      return number;
    }
    if (control === "json") {
      if (typeof raw === "object") return raw;
      try {
        return JSON.parse(raw);
      } catch (err) {
        throw new Error((field.title || field.name) + " must be valid JSON");
      }
    }
    if (control === "url_list") {
      if (Array.isArray(raw)) return raw;
      var urls = String(raw).split(/[\n,]+/).map(function (x) { return x.trim(); }).filter(Boolean);
      return urls.length ? urls : undefined;
    }
    if (control === "select") {
      if (Array.isArray(field.enum)) {
        for (var i = 0; i < field.enum.length; i += 1) {
          if (String(field.enum[i]) === String(raw)) return field.enum[i];
        }
      }
      return raw;
    }
    if (control === "text" || control === "url") {
      if (typeof raw === "string") {
        if (raw.trim() === "") return undefined;
        return raw.trim();
      }
      return raw;
    }
    return raw;
  }

  function buildParams(fields, values) {
    var params = {};
    (fields || []).forEach(function (field) {
      if (!field || !field.name) return;
      if (!Object.prototype.hasOwnProperty.call(values || {}, field.name)) return;
      var value = coerceValue(field, values[field.name]);
      if (value === undefined) return;
      if (field.control === "toggle") {
        params[field.name] = !!value;
        return;
      }
      params[field.name] = value;
    });
    return params;
  }

  function missingRequired(fields, values) {
    return (fields || []).filter(function (field) {
      if (!field || !field.required) return false;
      var raw = (values || {})[field.name];
      if (field.control === "toggle") return raw === undefined || raw === null;
      if (typeof raw === "string") return raw.trim() === "";
      return raw === undefined || raw === null;
    }).map(function (field) { return field.name; });
  }

  function promptField(fields) {
    return (fields || []).filter(function (field) {
      return field && field.name === "prompt" && field.control === "text";
    })[0] || null;
  }

  function basicFields(fields) {
    return (fields || []).filter(function (field) { return field && !field.advanced; });
  }

  function advancedFields(fields) {
    return (fields || []).filter(function (field) { return field && field.advanced; });
  }

  function acceptHint(model) {
    var summary = (model && model.summary) || {};
    var accepts = summary.accepts || {};
    var parts = [];
    if (accepts.image) parts.push("image in");
    if (accepts.video) parts.push("video in");
    if (accepts.audio) parts.push("audio in");
    if (summary.durations && summary.durations.length) {
      parts.push("duration " + summary.durations.join("/") + "s");
    }
    return parts.join(" · ");
  }

  function mediaUrl(mediaId, token) {
    if (!mediaId) return "";
    var base = "/api/studio/media/" + encodeURIComponent(mediaId);
    return token ? base + "?token=" + encodeURIComponent(token) : base;
  }

  function primaryOutput(job) {
    var outputs = (job && job.outputs) || [];
    for (var i = 0; i < outputs.length; i += 1) {
      if (outputs[i] && outputs[i].media_id) return outputs[i];
    }
    return outputs[0] || null;
  }

  function outputKind(job, output) {
    if (output && output.kind) return output.kind;
    return (job && job.kind) || "file";
  }

  function providerUsable(provider) {
    return !!provider && provider.configured === true;
  }

  function parseEnv(text) {
    var trimmed = String(text || "").trim();
    if (!trimmed) return {};
    var parsed = JSON.parse(trimmed);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Environment must be a JSON object");
    }
    return parsed;
  }

  function parseArgs(text) {
    var raw = String(text || "").trim();
    if (!raw) return [];
    if (raw.charAt(0) === "[") {
      var parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) throw new Error("Arguments must be a JSON array");
      return parsed.map(function (x) { return String(x); });
    }
    if (raw.charAt(0) === "{") throw new Error("Arguments must be an array");
    return raw.split(/\s+/).filter(Boolean);
  }

  function relativeTime(seconds) {
    var value = Math.max(0, Math.floor(Number(seconds) || 0));
    if (value < 5) return "just now";
    if (value < 60) return value + "s ago";
    if (value < 3600) return Math.floor(value / 60) + "m ago";
    if (value < 86400) return Math.floor(value / 3600) + "h ago";
    return Math.floor(value / 86400) + "d ago";
  }

  return {
    STATE_LABELS: STATE_LABELS,
    elapsedLabel: elapsedLabel,
    clockLabel: clockLabel,
    humanBytes: humanBytes,
    stateLabel: stateLabel,
    stateTone: stateTone,
    isActive: isActive,
    jobTitle: jobTitle,
    jobSummary: jobSummary,
    jobErrorText: jobErrorText,
    normalizeList: normalizeList,
    filterModels: filterModels,
    inputModeForModel: inputModeForModel,
    filterModelsByInput: filterModelsByInput,
    findById: findById,
    fieldDefault: fieldDefault,
    defaultValues: defaultValues,
    coerceValue: coerceValue,
    buildParams: buildParams,
    valuesFromParams: valuesFromParams,
    missingRequired: missingRequired,
    promptField: promptField,
    basicFields: basicFields,
    advancedFields: advancedFields,
    acceptHint: acceptHint,
    mediaUrl: mediaUrl,
    primaryOutput: primaryOutput,
    jobOutput: jobOutput,
    outputKind: outputKind,
    providerUsable: providerUsable,
    parseEnv: parseEnv,
    parseArgs: parseArgs,
    relativeTime: relativeTime,
    startOfToday: startOfToday,
    countJobsSince: countJobsSince,
    timestampLabel: timestampLabel
  };
});
