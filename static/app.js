/**
 * Brand Scraper SPA — vanilla JS, no build step.
 *
 * Auth: real mode uses Google ID Services, sends id_token as Bearer.
 * Mock mode (no GOOGLE_CLIENT_ID configured): caller types an email,
 * which the backend trusts only if the env-controlled allowlist contains
 * it. Mock mode is fail-closed in prod by definition.
 *
 * State machine:
 *   bootstrapping → signed_out → signed_in
 *                                     → home (brand list + scrape form)
 *                                     → brand (DNA + asset menu)
 */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const h = (tag, attrs = {}, children = []) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) el.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return el;
};

const state = {
  config: null,        // /api/config response
  user: null,          // { email, name, picture, token? }
  authToken: null,     // Google id_token (real) or mock email (mock)
  brands: [],
  currentBrand: null,  // { manifest, brand_dna, outputs }
};

const API = {
  async _fetch(path, opts = {}) {
    const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    if (state.authToken) {
      if (state.config?.mock_mode) headers["X-Mock-Email"] = state.authToken;
      else headers["Authorization"] = `Bearer ${state.authToken}`;
    }
    const r = await fetch(path, { ...opts, headers });
    if (!r.ok) {
      let msg = `${r.status} ${r.statusText}`;
      try { const body = await r.json(); msg = body.detail || msg; } catch {}
      throw new Error(msg);
    }
    return r.json();
  },
  config()                                 { return this._fetch("/api/config"); },
  me()                                     { return this._fetch("/api/me"); },
  brands()                                 { return this._fetch("/api/brands"); },
  scrape(handle, posts)                    { return this._fetch("/api/scrape",   { method: "POST", body: JSON.stringify({ handle, posts }) }); },
  brand(handle)                            { return this._fetch(`/api/brand/${encodeURIComponent(handle)}`); },
  extractDna(handle)                       { return this._fetch(`/api/brand/${encodeURIComponent(handle)}/dna`); },
  generate(handle, asset_type, audience_or_goal, constraints) {
    return this._fetch("/api/generate", { method: "POST", body: JSON.stringify({ handle, asset_type, audience_or_goal, constraints }) });
  },
  buildCatalog(handle)                     { return this._fetch(`/api/brand/${encodeURIComponent(handle)}/catalog`, { method: "POST" }); },
  adminList()                              { return this._fetch("/api/admin/allowlist"); },
  adminAdd(email)                          { return this._fetch("/api/admin/allowlist", { method: "POST", body: JSON.stringify({ email }) }); },
  adminRemove(email)                       { return this._fetch(`/api/admin/allowlist/${encodeURIComponent(email)}`, { method: "DELETE" }); },
};

// ── Markdown rendering — minimal, no deps ──
function mdToHtml(md) {
  if (!md) return "";
  let s = md
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  // code fences (preserve content)
  s = s.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, body) =>
    `<pre class="bg-slate-100 rounded p-3 text-xs overflow-x-auto scrollbar-thin"><code>${body}</code></pre>`);
  // headings
  s = s.replace(/^### (.*)$/gm, '<h3 class="font-semibold text-base mt-4 mb-1">$1</h3>')
       .replace(/^## (.*)$/gm,  '<h2 class="font-semibold text-lg mt-5 mb-2">$1</h2>')
       .replace(/^# (.*)$/gm,   '<h1 class="font-bold text-xl mt-5 mb-2">$1</h1>');
  // bullets
  s = s.replace(/^(\s*)[-*] (.*)$/gm, '$1<li>$2</li>');
  s = s.replace(/(<li>.*<\/li>\n?)+/g, m => `<ul class="list-disc pl-5 space-y-0.5 my-2">${m}</ul>`);
  // bold + italic
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
       .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
  // line breaks (paragraphs)
  s = s.split(/\n{2,}/).map(p => p.match(/^<(h\d|ul|pre|ol)/) ? p : `<p class="my-2">${p}</p>`).join("\n");
  return s;
}

// ── Views ──
function renderShell(content) {
  const root = $("#app");
  root.innerHTML = "";
  root.appendChild(h("div", { class: "max-w-5xl mx-auto p-6" }, [
    h("header", { class: "flex items-center justify-between mb-6" }, [
      h("h1", { class: "text-xl font-bold tracking-tight" }, "🧬 Brand Scraper"),
      state.user
        ? h("div", { class: "flex items-center gap-3 text-sm" }, [
            state.user.picture ? h("img", { src: state.user.picture, class: "w-7 h-7 rounded-full" }) : null,
            h("span", { class: "text-slate-600" }, state.user.email),
            h("button", {
              class: "text-slate-500 hover:text-slate-900 text-xs",
              onclick: () => { state.user = null; state.authToken = null; renderSignIn(); },
            }, "sign out"),
          ])
        : null,
    ]),
    content,
  ]));
}

function renderSignIn() {
  const card = h("div", { class: "bg-white rounded-xl shadow p-8 max-w-md mx-auto" }, []);
  card.appendChild(h("h2", { class: "text-lg font-semibold mb-1" }, "Sign in"));
  card.appendChild(h("p", { class: "text-sm text-slate-600 mb-5" },
    "Allowlist-only. Ask the owner to add your email."));

  if (state.config?.mock_mode) {
    card.appendChild(h("div", { class: "bg-amber-50 border border-amber-200 text-amber-900 text-xs rounded p-2 mb-3" },
      "Mock mode active (no GOOGLE_CLIENT_ID). Type an allowlisted email to continue."));
    const input = h("input", {
      type: "email",
      class: "w-full border rounded px-3 py-2 mb-3",
      placeholder: "you@example.com",
    });
    const errBox = h("div", { class: "text-xs text-red-600 mb-2 hidden" });
    const btn = h("button", {
      class: "w-full bg-slate-900 text-white py-2 rounded hover:bg-slate-700",
      onclick: async () => {
        const email = input.value.trim();
        if (!email) return;
        state.authToken = email;
        try {
          const me = await API.me();
          state.user = me;
          renderHome();
        } catch (e) {
          errBox.textContent = e.message;
          errBox.classList.remove("hidden");
          state.authToken = null;
        }
      },
    }, "Continue");
    card.appendChild(input);
    card.appendChild(errBox);
    card.appendChild(btn);
  } else {
    const btnHost = h("div", { id: "g-btn", class: "mt-3" });
    card.appendChild(btnHost);
    mountGoogleButton(btnHost);
  }
  renderShell(card);
}

function mountGoogleButton(host) {
  const clientId = state.config?.google_client_id;
  if (!clientId) return;
  const tryMount = () => {
    if (!window.google?.accounts?.id) return setTimeout(tryMount, 200);
    window.google.accounts.id.initialize({
      client_id: clientId,
      callback: async (response) => {
        if (!response.credential) return;
        state.authToken = response.credential;
        try {
          const me = await API.me();
          state.user = me;
          renderHome();
        } catch (e) {
          alert("Sign-in failed: " + e.message);
          state.authToken = null;
        }
      },
    });
    window.google.accounts.id.renderButton(host, { theme: "outline", size: "large", type: "standard" });
  };
  tryMount();
}

async function renderHome() {
  const wrapper = h("div", {});
  if (state.config?.data_root_ephemeral) {
    wrapper.appendChild(h("div", {
      class: "bg-amber-50 border border-amber-300 text-amber-900 rounded-lg p-3 mb-4 text-sm",
    }, [
      h("div", { class: "font-semibold mb-1" }, "⚠ Storage is ephemeral"),
      h("div", { class: "text-xs" },
        `DATA_ROOT=${state.config.data_root} — scraped data will be wiped on the next redeploy. ` +
        `On Railway: mount a volume at /data and set the DATA_ROOT env var to /data.`),
    ]));
  }
  wrapper.appendChild(renderScrapeCard());
  wrapper.appendChild(h("h2", { class: "text-sm uppercase tracking-wide text-slate-500 mt-8 mb-3" }, "Brands"));
  const list = h("div", { class: "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3" }, [
    h("p", { class: "text-slate-500 text-sm" }, "Loading…"),
  ]);
  wrapper.appendChild(list);
  renderShell(wrapper);

  try {
    const { brands } = await API.brands();
    state.brands = brands;
    list.innerHTML = "";
    if (!brands.length) {
      list.appendChild(h("p", { class: "text-slate-500 text-sm col-span-full" },
        "No brands yet. Scrape one above."));
    } else {
      for (const b of brands) {
        list.appendChild(h("button", {
          class: "text-left bg-white rounded-lg shadow p-4 hover:shadow-md transition",
          onclick: () => openBrand(b.handle),
        }, [
          h("div", { class: "font-semibold" }, "@" + b.handle),
          h("div", { class: "text-xs text-slate-500" }, b.fullName || ""),
          h("div", { class: "text-xs text-slate-400 mt-2" },
            `${(b.followers || 0).toLocaleString()} followers · ${b.post_count} posts scraped`),
        ]));
      }
    }
  } catch (e) {
    list.innerHTML = `<p class="text-red-600 text-sm">${e.message}</p>`;
  }

  if (state.user?.is_admin) {
    wrapper.appendChild(await renderAdminCard());
  }
}

async function renderAdminCard() {
  const card = h("div", { class: "bg-white rounded-xl shadow p-5 mt-8" }, []);
  card.appendChild(h("h2", { class: "font-semibold mb-1" }, "Admin · Access control"));
  card.appendChild(h("p", { class: "text-xs text-slate-500 mb-4" },
    "Anyone on this list can sign in. The owner is always allowed and can't be removed."));

  const listHost = h("div", { class: "mb-4" }, [h("p", { class: "text-slate-500 text-sm" }, "Loading…")]);
  card.appendChild(listHost);

  const input = h("input", {
    type: "email",
    class: "flex-1 border rounded-l px-3 py-2 text-sm",
    placeholder: "alice@example.com",
  });
  const errBox = h("p", { class: "text-xs text-red-600 mt-2 hidden" });
  const addBtn = h("button", {
    class: "bg-slate-900 text-white px-4 py-2 rounded-r text-sm hover:bg-slate-700 disabled:opacity-50",
    onclick: async () => {
      const email = input.value.trim();
      if (!email) return;
      addBtn.disabled = true;
      errBox.classList.add("hidden");
      try {
        const data = await API.adminAdd(email);
        input.value = "";
        renderAllowlistEntries(listHost, data);
      } catch (e) {
        errBox.textContent = e.message;
        errBox.classList.remove("hidden");
      } finally {
        addBtn.disabled = false;
      }
    },
  }, "Add");
  card.appendChild(h("div", { class: "flex" }, [input, addBtn]));
  card.appendChild(errBox);

  try {
    const data = await API.adminList();
    renderAllowlistEntries(listHost, data);
  } catch (e) {
    listHost.innerHTML = `<p class="text-red-600 text-sm">${e.message}</p>`;
  }
  return card;
}

function renderAllowlistEntries(host, data) {
  host.innerHTML = "";
  const rows = [
    h("div", { class: "flex items-center py-2 border-b text-sm" }, [
      h("div", { class: "flex-1 font-medium" }, data.owner),
      h("div", { class: "text-xs text-slate-400 mr-3" }, "owner"),
      h("div", { class: "text-xs text-slate-300 w-16 text-right" }, "—"),
    ]),
  ];
  if (!data.entries.length) {
    rows.push(h("p", { class: "text-xs text-slate-400 mt-3" }, "No additional users yet."));
  }
  for (const e of data.entries) {
    rows.push(h("div", { class: "flex items-center py-2 border-b text-sm" }, [
      h("div", { class: "flex-1" }, e.email),
      h("div", { class: "text-xs text-slate-400 mr-3" }, e.added_at?.slice(0, 10) || ""),
      h("button", {
        class: "text-xs text-red-600 hover:underline w-16 text-right",
        onclick: async () => {
          if (!confirm(`Revoke access for ${e.email}?`)) return;
          try {
            const data2 = await API.adminRemove(e.email);
            renderAllowlistEntries(host, data2);
          } catch (err) { alert(err.message); }
        },
      }, "remove"),
    ]));
  }
  for (const r of rows) host.appendChild(r);
}

function renderScrapeCard() {
  const handleInput = h("input", {
    type: "text",
    class: "flex-1 border rounded-l px-3 py-2",
    placeholder: "@handle",
  });
  const status = h("p", { class: "text-xs text-slate-500 mt-2" });
  const button = h("button", {
    class: "bg-slate-900 text-white px-4 py-2 rounded-r hover:bg-slate-700 disabled:opacity-50",
    onclick: async () => {
      const raw = handleInput.value.trim();
      if (!raw) return;
      button.disabled = true;
      status.className = "text-xs text-slate-500 mt-2";
      status.textContent = "Scraping… (Apify cold-start can take 60s)";
      try {
        const r = await API.scrape(raw, state.config.max_posts);
        status.textContent = `Done. ${r.posts_fetched} posts, ${r.images_downloaded}/${r.images_total} images.`;
        await renderHome();
        setTimeout(() => openBrand(r.handle), 50);
      } catch (e) {
        status.className = "text-xs text-red-600 mt-2";
        status.textContent = e.message;
      } finally {
        button.disabled = false;
      }
    },
  }, "Scrape");

  return h("div", { class: "bg-white rounded-xl shadow p-5" }, [
    h("h2", { class: "font-semibold mb-3" }, "Scrape a new brand"),
    h("div", { class: "flex" }, [handleInput, button]),
    status,
  ]);
}

async function openBrand(handle) {
  const wrapper = h("div", {});
  wrapper.appendChild(h("button", {
    class: "text-sm text-slate-500 hover:text-slate-900 mb-3",
    onclick: renderHome,
  }, "← all brands"));
  wrapper.appendChild(h("p", { class: "text-slate-500 text-sm" }, "Loading…"));
  renderShell(wrapper);

  try {
    const data = await API.brand(handle);
    state.currentBrand = data;
    renderBrand(handle, data);
  } catch (e) {
    wrapper.innerHTML = `<p class="text-red-600 text-sm">${e.message}</p>`;
  }
}

function renderBrand(handle, data) {
  const { manifest, brand_dna, catalog_summary, outputs } = data;
  const ps = manifest.profile_summary || {};
  const wrapper = h("div", {});
  wrapper.appendChild(h("button", {
    class: "text-sm text-slate-500 hover:text-slate-900 mb-3 inline-block",
    onclick: renderHome,
  }, "← all brands"));

  const header = h("div", { class: "bg-white rounded-xl shadow p-5 mb-4 flex gap-4 items-start" }, [
    h("img", {
      src: `/data/${handle}/profile_pic.jpg`,
      class: "w-16 h-16 rounded-full object-cover bg-slate-200",
      onerror: function () { this.style.display = "none"; },
    }),
    h("div", { class: "flex-1" }, [
      h("div", { class: "font-bold text-lg" }, "@" + handle),
      h("div", { class: "text-sm text-slate-700" }, ps.fullName || ""),
      h("div", { class: "text-xs text-slate-500 mt-1" },
        `${(ps.followersCount || 0).toLocaleString()} followers · ${ps.postsCount || 0} posts on IG · ${manifest.post_count} scraped`),
      ps.biography ? h("p", { class: "text-sm text-slate-600 mt-2 whitespace-pre-line" }, ps.biography) : null,
    ]),
  ]);
  wrapper.appendChild(header);

  // Image catalog panel
  const catCard = h("div", { class: "bg-white rounded-xl shadow p-5 mb-4" }, []);
  catCard.appendChild(h("div", { class: "flex items-center justify-between mb-2" }, [
    h("h2", { class: "font-semibold" }, "Image catalog"),
    h("button", {
      class: "text-xs text-slate-500 hover:text-slate-900",
      onclick: async () => {
        const status = catCard.querySelector("[data-cat-status]");
        status.textContent = "Cataloging… (~$0.001/image, ~30s)";
        try {
          const r = await API.buildCatalog(handle);
          status.textContent = `Done. Indexed ${r.image_count} images.`;
          const fresh = await API.brand(handle);
          state.currentBrand = fresh;
          renderBrand(handle, fresh);
        } catch (e) {
          status.className = "text-xs text-red-600 mt-1";
          status.textContent = e.message;
        }
      },
    }, catalog_summary ? "re-catalog" : "build catalog"),
  ]));
  if (catalog_summary) {
    catCard.appendChild(h("div", { class: "text-xs text-slate-600" }, [
      h("span", { class: "font-medium" }, `${catalog_summary.image_count} images indexed `),
      h("span", { class: "text-slate-400" },
        `(${Object.entries(catalog_summary.by_quality).map(([k,v]) => `${v} ${k}`).join(", ")})`),
    ]));
    catCard.appendChild(h("div", { class: "text-xs text-slate-500 mt-1" },
      "Layouts: " + Object.entries(catalog_summary.by_layout).map(([k,v]) => `${k}×${v}`).join(", ")));
  } else {
    catCard.appendChild(h("p", { class: "text-xs text-slate-500" },
      "Not built yet. Will auto-build on first DNA extract, or click 'build catalog'."));
  }
  catCard.appendChild(h("p", { class: "text-xs text-slate-500 mt-2", "data-cat-status": "" }));
  wrapper.appendChild(catCard);

  // Image gallery
  const galleryCard = h("div", { class: "bg-white rounded-xl shadow p-5 mb-4" }, []);
  galleryCard.appendChild(h("div", { class: "flex items-center justify-between mb-3" }, [
    h("h2", { class: "font-semibold" }, "Image gallery"),
    h("span", { class: "text-xs text-slate-500" }, "click any to open full size"),
  ]));
  const galleryHost = h("div", {
    class: "grid grid-cols-3 sm:grid-cols-5 lg:grid-cols-8 gap-2",
  });
  galleryCard.appendChild(galleryHost);
  loadGallery(handle, galleryHost, !!catalog_summary);
  wrapper.appendChild(galleryCard);

  // BRAND_DNA panel
  const dnaCard = h("div", { class: "bg-white rounded-xl shadow p-5 mb-4" }, []);
  dnaCard.appendChild(h("div", { class: "flex items-center justify-between mb-3" }, [
    h("h2", { class: "font-semibold" }, "BRAND_DNA"),
    h("button", {
      class: "text-xs text-slate-500 hover:text-slate-900",
      onclick: async () => {
        dnaCard.querySelector("[data-dna]").innerHTML = '<p class="text-slate-500 text-sm">Re-extracting…</p>';
        try {
          const dna = await API.extractDna(handle);
          state.currentBrand.brand_dna = dna;
          renderDna(dnaCard.querySelector("[data-dna]"), dna);
        } catch (e) {
          dnaCard.querySelector("[data-dna]").innerHTML = `<p class="text-red-600 text-sm">${e.message}</p>`;
        }
      },
    }, brand_dna ? "re-extract" : "extract"),
  ]));
  const dnaBody = h("div", { "data-dna": "" });
  if (brand_dna) renderDna(dnaBody, brand_dna);
  else dnaBody.appendChild(h("p", { class: "text-slate-500 text-sm" },
    "Not extracted yet. Click 'extract' or generate any asset (it'll auto-extract)."));
  dnaCard.appendChild(dnaBody);
  wrapper.appendChild(dnaCard);

  // Generate panel
  wrapper.appendChild(renderGenerateCard(handle));

  // Past outputs
  if (outputs && outputs.length) {
    const outCard = h("div", { class: "bg-white rounded-xl shadow p-5 mt-4" }, [
      h("h2", { class: "font-semibold mb-2" }, "Past outputs"),
      h("ul", { class: "text-sm space-y-1" },
        outputs.map(o => h("li", {}, [
          h("a", {
            href: `/data/${o.dir}/asset.md`,
            target: "_blank",
            class: "text-blue-600 hover:underline",
          }, `${o.asset_type || "asset"} — ${o.created_at || ""}`),
          o.has_html ? h("a", {
            href: `/data/${o.dir}/asset.html`,
            target: "_blank",
            class: "ml-2 text-xs text-slate-500 hover:underline",
          }, "[html]") : null,
          o.has_svg ? h("a", {
            href: `/data/${o.dir}/asset.svg`,
            target: "_blank",
            class: "ml-2 text-xs text-slate-500 hover:underline",
          }, "[svg]") : null,
        ]))
      ),
    ]);
    wrapper.appendChild(outCard);
  }

  renderShell(wrapper);
}

async function loadGallery(handle, host, hasCatalog) {
  host.innerHTML = '<p class="text-xs text-slate-500 col-span-full">loading…</p>';
  if (hasCatalog) {
    try {
      const r = await fetch(`/data/${encodeURIComponent(handle)}/image_catalog.json`);
      const cat = await r.json();
      host.innerHTML = "";
      const sorted = (cat.images || []).slice().sort((a, b) =>
        (b.post_likes || 0) - (a.post_likes || 0));
      for (const img of sorted) {
        host.appendChild(galleryThumb(handle, img));
      }
      if (!sorted.length) {
        host.innerHTML = '<p class="text-xs text-slate-500 col-span-full">catalog has no entries — try re-cataloging</p>';
      }
    } catch (e) {
      host.innerHTML = `<p class="text-xs text-red-600 col-span-full">failed to load catalog: ${e.message}</p>`;
    }
    return;
  }
  // Fallback when catalog isn't built yet — list image files directly.
  try {
    const r = await API._fetch(`/api/brand/${encodeURIComponent(handle)}/files`);
    host.innerHTML = "";
    const imgFiles = (r.files || []).filter(f => f.path.startsWith("images/") && f.size > 0);
    for (const f of imgFiles) {
      host.appendChild(galleryThumb(handle, {
        shortcode: f.path.replace("images/", "").replace(".jpg", ""),
        image_path: f.path,
      }));
    }
    if (!imgFiles.length) {
      host.innerHTML = '<p class="text-xs text-slate-500 col-span-full">no images yet — re-scrape</p>';
    }
  } catch (e) {
    host.innerHTML = `<p class="text-xs text-red-600 col-span-full">${e.message}</p>`;
  }
}

function galleryThumb(handle, img) {
  const url = `/data/${encodeURIComponent(handle)}/${img.image_path}`;
  const quality = img.design_quality;
  const badgeClass = quality === "high"
    ? "bg-emerald-500" : quality === "low" ? "bg-red-500" : "bg-slate-500";
  const tooltip = [
    `@${handle}/${img.shortcode}`,
    img.subject ? `\n${img.subject}` : "",
    img.layout ? `\nlayout: ${img.layout}` : "",
    img.mood ? `\nmood: ${img.mood}` : "",
    img.post_likes ? `\nlikes: ${img.post_likes}` : "",
    (img.usable_for || []).length ? `\nusable for: ${img.usable_for.join(", ")}` : "",
  ].join("");
  const wrap = h("a", {
    href: url,
    target: "_blank",
    class: "relative block aspect-square overflow-hidden rounded bg-slate-100 group",
    title: tooltip,
  }, [
    h("img", {
      src: url,
      class: "w-full h-full object-cover group-hover:scale-105 transition",
      loading: "lazy",
    }),
  ]);
  if (quality) {
    wrap.appendChild(h("span", {
      class: `absolute top-1 right-1 px-1.5 rounded text-[10px] font-bold text-white ${badgeClass}`,
      title: `design quality: ${quality}`,
    }, quality.charAt(0).toUpperCase()));
  }
  if (img.post_likes) {
    wrap.appendChild(h("span", {
      class: "absolute bottom-1 left-1 px-1.5 rounded text-[10px] font-medium text-white bg-black/50",
    }, `♥ ${img.post_likes.toLocaleString()}`));
  }
  return wrap;
}

function renderDna(host, dna) {
  host.innerHTML = "";
  const v = dna.visual_system || {};
  const colors = (v.dominant_colors_hex || []).map(c => h("span", {
    class: "inline-block w-6 h-6 rounded border border-slate-200 mr-1",
    style: `background:${c}`,
    title: c,
  }));
  host.appendChild(h("div", { class: "mb-3" }, [
    h("div", { class: "text-xs text-slate-500" }, "PALETTE"),
    h("div", { class: "mt-1 flex items-center" }, colors),
    h("div", { class: "text-xs text-slate-500 mt-1" }, (v.dominant_colors_hex || []).join("  ·  ")),
  ]));
  host.appendChild(h("div", { class: "mb-3" }, [
    h("div", { class: "text-xs text-slate-500" }, "POSITIONING"),
    h("div", { class: "text-sm" }, (dna.identity || {}).positioning_one_liner || "—"),
  ]));
  const pre = h("pre", {
    class: "bg-slate-50 border rounded p-3 text-xs overflow-x-auto scrollbar-thin",
  }, JSON.stringify(dna, null, 2));
  const details = h("details", { class: "mt-2" }, [
    h("summary", { class: "text-xs text-slate-500 cursor-pointer" }, "full JSON"),
    pre,
  ]);
  host.appendChild(details);
}

function renderGenerateCard(handle) {
  const card = h("div", { class: "bg-white rounded-xl shadow p-5" }, []);
  card.appendChild(h("h2", { class: "font-semibold mb-3" }, "Generate marketing material"));

  const select = h("select", { class: "w-full border rounded px-3 py-2 mb-2" },
    state.config.asset_types.map(t => h("option", { value: t }, t)));
  const audience = h("input", {
    type: "text", class: "w-full border rounded px-3 py-2 mb-2",
    placeholder: "Audience / goal (e.g. 'Mother's Day brunch bookings')",
  });
  const constraints = h("input", {
    type: "text", class: "w-full border rounded px-3 py-2 mb-2",
    placeholder: "Constraints (e.g. 'pt-BR only, max 7 slides, no discounts')",
  });
  const status = h("p", { class: "text-xs text-slate-500 mt-2" });
  const result = h("div", { class: "mt-4" });
  const button = h("button", {
    class: "bg-slate-900 text-white px-4 py-2 rounded hover:bg-slate-700 disabled:opacity-50",
    onclick: async () => {
      button.disabled = true;
      status.className = "text-xs text-slate-500 mt-2";
      status.textContent = "Generating… (BRAND_DNA extraction takes ~20s on first asset; then assets ~10s)";
      result.innerHTML = "";
      try {
        const r = await API.generate(handle, select.value, audience.value, constraints.value);
        status.textContent = `Saved to ${r.output_dir}`;
        renderResult(result, r, handle);
        // refresh outputs sidebar
        const fresh = await API.brand(handle);
        state.currentBrand = fresh;
      } catch (e) {
        status.className = "text-xs text-red-600 mt-2";
        status.textContent = e.message;
      } finally {
        button.disabled = false;
      }
    },
  }, "Generate");
  card.appendChild(select);
  card.appendChild(audience);
  card.appendChild(constraints);
  card.appendChild(button);
  card.appendChild(status);
  card.appendChild(result);
  return card;
}

function renderResult(host, r, handle) {
  host.innerHTML = "";
  if (r.html) {
    host.appendChild(h("div", { class: "mb-2 text-xs text-slate-500" }, "HTML preview:"));
    const iframe = h("iframe", {
      srcdoc: r.html,
      class: "w-full h-[600px] border rounded",
      sandbox: "allow-same-origin",
    });
    host.appendChild(iframe);
  }
  if (r.svg) {
    host.appendChild(h("div", { class: "mb-2 mt-3 text-xs text-slate-500" }, "SVG:"));
    const wrap = h("div", { class: "border rounded p-3 bg-white", html: r.svg });
    host.appendChild(wrap);
  }
  host.appendChild(h("div", { class: "mb-2 mt-3 text-xs text-slate-500" }, "Markdown:"));
  const md = h("div", { class: "prose prose-sm max-w-none border rounded p-4 bg-slate-50", html: mdToHtml(r.markdown) });
  host.appendChild(md);
  host.appendChild(h("div", { class: "text-xs mt-2" }, [
    h("a", { class: "text-blue-600 hover:underline", target: "_blank", href: `/data/${r.output_dir}/asset.md` },
      "open raw asset.md"),
  ]));
}

// ── Bootstrap ──
(async function init() {
  try {
    state.config = await API.config();
  } catch (e) {
    document.body.innerHTML = `<p class="p-6 text-red-600">Bootstrap failed: ${e.message}</p>`;
    return;
  }
  renderSignIn();
})();
