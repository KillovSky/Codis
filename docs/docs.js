/* Busca, copiar código, destaque do índice e menu móvel da documentação. */
(function () {
  "use strict";
  var q = document.getElementById("q"), box = document.getElementById("results"), idx = null, sel = -1;
  function load() {
    if (idx) return Promise.resolve(idx);
    return fetch("search-index.json").then(function (r) { return r.json(); }).then(function (d) { idx = d; return d; });
  }
  function norm(s) { return s.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, ""); }
  function esc(s) { return s.replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function run() {
    var term = norm(q.value.trim());
    if (term.length < 2) { box.hidden = true; return; }
    load().then(function (d) {
      var words = term.split(/\s+/), out = [];
      d.forEach(function (p) {
        var t = norm(p.t), h = norm(p.h.join(" ")), x = norm(p.x), score = 0, ok = true;
        words.forEach(function (w) {
          var s = (t.indexOf(w) >= 0 ? 8 : 0) + (h.indexOf(w) >= 0 ? 4 : 0) + (x.indexOf(w) >= 0 ? 1 : 0);
          if (!s) ok = false; score += s;
        });
        if (ok) {
          var at = x.indexOf(words[0]), snip = at >= 0 ? p.x.substr(Math.max(0, at - 40), 130) : p.x.substr(0, 110);
          out.push({ p: p, score: score, snip: snip });
        }
      });
      out.sort(function (a, b) { return b.score - a.score; });
      box.innerHTML = out.length ? out.slice(0, 8).map(function (r) {
        return '<li><a href="' + r.p.u + '"><b>' + esc(r.p.t) + '</b><small>' + esc(r.snip) + "…</small></a></li>";
      }).join("") : '<li class="none">Nada encontrado.</li>';
      box.hidden = false; sel = -1;
    });
  }
  if (q) {
    q.addEventListener("input", run);
    q.addEventListener("keydown", function (e) {
      var links = box.querySelectorAll("a");
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!links.length) return;
        sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
        links.forEach(function (a, i) { a.classList.toggle("sel", i === sel); });
      } else if (e.key === "Enter" && sel >= 0) { links[sel].click(); }
      else if (e.key === "Escape") { box.hidden = true; q.blur(); }
    });
    document.addEventListener("click", function (e) { if (!box.contains(e.target) && e.target !== q) box.hidden = true; });
    document.addEventListener("keydown", function (e) {
      if (e.key === "/" && !/^(input|textarea)$/i.test(document.activeElement.tagName)) { e.preventDefault(); q.focus(); }
    });
  }
  document.querySelectorAll(".prose pre").forEach(function (pre) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "copy"; b.textContent = "Copiar";
    b.addEventListener("click", function () {
      var code = pre.querySelector("code");
      navigator.clipboard.writeText((code || pre).innerText).then(function () {
        b.textContent = "Copiado"; setTimeout(function () { b.textContent = "Copiar"; }, 1500);
      });
    });
    pre.appendChild(b);
  });
  var tog = document.querySelector(".rail-toggle"), rail = document.getElementById("rail");
  if (tog) tog.addEventListener("click", function () {
    var o = rail.classList.toggle("open"); tog.setAttribute("aria-expanded", String(o));
  });
  var links = [].slice.call(document.querySelectorAll(".toc a"));
  if (links.length && "IntersectionObserver" in window) {
    var map = {};
    links.forEach(function (a) { map[a.getAttribute("href").slice(1)] = a; });
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (e.isIntersecting && map[e.target.id]) {
          links.forEach(function (a) { a.classList.remove("on"); }); map[e.target.id].classList.add("on");
        }
      });
    }, { rootMargin: "-72px 0px -70% 0px" });
    Object.keys(map).forEach(function (id) { var el = document.getElementById(id); if (el) io.observe(el); });
  }
})();
