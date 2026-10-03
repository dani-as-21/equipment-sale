/* Matching rules shared with the offline capture screen.
   Exact unambiguous identifiers may auto-link. Names, models, and partial
   tags are suggestions only. */
(function () {
  const NON_IDS = { "": 1, "-": 1, "--": 1, "—": 1, "N/A": 1, NA: 1, NONE: 1, NULL: 1 };
  const UNKNOWN = { "": 1, "לא ידוע": 1, unknown: 1 };

  function normId(value) {
    const text = String(value || "").trim().toUpperCase();
    if (NON_IDS[text]) return "";
    return text;
  }

  function normArea(value) {
    return String(value || "").trim().toLowerCase();
  }

  function areaConflict(observed, listed, parent) {
    const o = normArea(observed);
    const l = normArea(listed);
    const p = normArea(parent);
    if (UNKNOWN[o] || UNKNOWN[l]) return false;
    if (o === l) return false;
    if (p && p === l) return false;
    return true;
  }

  function suggestion(row, why) {
    return {
      row_id: row.id,
      tag: row.tag_original || "",
      description: row.description || "",
      sheet_name: row.sheet_name || "",
      original_row: row.original_row,
      listed_area: row.listed_area || "",
      identifier: row.tag_original || row.serial || "",
      why: why,
    };
  }

  function collapse(value) {
    return String(value || "").replace(/\s+/g, "");
  }

  function fromHits(hits, observedArea, observedParent, label) {
    const groups = new Set(hits.map((hit) => hit.duplicate_group || ""));
    const areas = new Set(hits.map((hit) => hit.identity_area || ""));
    const identical = hits.length > 1 && areas.size === 1 && groups.size === 1 && [...groups][0];
    const conflict = hits.some((hit) => areaConflict(observedArea, hit.listed_area, observedParent));
    const suggestions = hits.slice(0, 20).map((hit) => suggestion(hit, "התאמה לפי " + label + ". אזור ברשימה: " + (hit.listed_area || "לא ידוע") + "."));
    if (hits.length === 1 || identical) {
      let reason = identical
        ? "אותו תג באותו אזור, עם אותם נתונים מלאים. זו כפילות משוערת: שורות המקור נשמרות, והצילום מקושר לכולן."
        : "ה" + label + " זהה לשורה אחת, ואין סתירה עם האזור שנצפה.";
      if (conflict) {
        const listed = hits[0].listed_area || "לא ידוע";
        const observed = observedArea || "לא ידוע";
        reason = "ה" + label + " נמצא, אבל האזור בסיור (" + observed + ") שונה מהאזור שברשימה (" + listed + "). ההתאמה ממתינה לבדיקה ולא הוחלפה בשקט.";
        return {
          mode: "pending",
          links: hits.map((hit) => ({ row_id: hit.id, method: "proposed", review_status: "pending_review", reason: reason })),
          suggestions: suggestions,
          review_kind: "location",
          explanation: reason,
        };
      }
      return {
        mode: "auto",
        links: hits.map((hit) => ({ row_id: hit.id, method: "auto", review_status: "auto_linked", reason: reason })),
        suggestions: suggestions,
        review_kind: null,
        explanation: reason,
      };
    }
    const reason = "ה" + label + " מופיע בכמה שורות עם נתונים שונים או באזורים שונים. לא נבחרה שורה אוטומטית ולא אוחדו רשומות.";
    return { mode: "pending", links: [], suggestions: suggestions, review_kind: "duplicate_tag", explanation: reason };
  }

  function fuzzyOrName(rows, tagN, rawTag) {
    const collapsed = collapse(tagN);
    const nameKey = String(rawTag || "").trim().toLowerCase();
    const found = [];
    for (const row of rows) {
      const tagRow = row.tag_norm || "";
      let why = "";
      if (collapsed && tagRow && collapse(tagRow) === collapsed && tagRow !== tagN) {
        why = "התג זהה רק אחרי מחיקת רווחים. התווים המקוריים נשמרו, ואין קישור אוטומטי.";
      } else if (tagRow && tagN && tagRow !== tagN && Math.min(tagRow.length, tagN.length) >= 4 && (tagRow.startsWith(tagN) || tagN.startsWith(tagRow))) {
        why = "התג דומה רק בחלק מהתווים. אין קישור אוטומטי.";
      } else if (nameKey && String(row.description || "").trim().toLowerCase() === nameKey) {
        why = "השם זהה, בלי תג תואם. אין קישור אוטומטי לפי שם או לפי מראה.";
      }
      if (why) found.push(suggestion(row, why));
      if (found.length >= 20) break;
    }
    return found;
  }

  function matchInventory(rows, input) {
    const equipment = (rows || []).filter((row) => (row.kind || "equipment") === "equipment");
    const tagN = normId(input.tag);
    const serialN = normId(input.serial);
    const modelN = normId(input.model);
    const observed = input.observed_area || "";
    const parent = input.observed_parent || "";
    if (tagN) {
      const hits = equipment.filter((row) => row.tag_norm === tagN);
      if (hits.length) return fromHits(hits, observed, parent, "תג");
      const fuzzy = fuzzyOrName(equipment, tagN, input.tag);
      if (fuzzy.length) {
        return { mode: "pending", links: [], suggestions: fuzzy, review_kind: "ambiguous", explanation: fuzzy[0].why };
      }
    }
    if (serialN) {
      const hits = equipment.filter((row) => row.serial_norm === serialN);
      if (hits.length) return fromHits(hits, observed, parent, "מספר סידורי");
    }
    if (modelN) {
      const hits = equipment.filter((row) => normId(row.model) === modelN);
      if (hits.length) {
        return {
          mode: "pending",
          links: [],
          suggestions: hits.slice(0, 20).map((row) => suggestion(row, "הדגם זהה. דגם אינו תג זיהוי, ולכן אין קישור אוטומטי.")),
          review_kind: "ambiguous",
          explanation: "נמצאו שורות עם אותו דגם. צריך לבחור ידנית, או להשאיר בלי התאמה.",
        };
      }
    }
    return {
      mode: "none",
      links: [],
      suggestions: [],
      review_kind: null,
      explanation: "לא נמצאה התאמה. הפריט יישמר כנצפה בסיור, בלי להכריז שהוא ציוד חדש במלאי.",
    };
  }

  window.CaptureMatch = { matchInventory, normId, areaConflict };
})();
