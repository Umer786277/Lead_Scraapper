"use client";

import { Check, ChevronDown, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { createPortal } from "react-dom";

interface SearchableSelectProps {
  value: string;
  onChange: (value: string) => void;
  options: string[];
  placeholder?: string;
  disabled?: boolean;
  ariaLabel?: string;
  emptyLabel?: string;
  searchPlaceholder?: string;
  maxResults?: number;
}

export default function SearchableSelect({
  value,
  onChange,
  options,
  placeholder = "Select…",
  disabled = false,
  ariaLabel,
  emptyLabel = "No options",
  searchPlaceholder = "Search…",
  maxResults = 250,
}: SearchableSelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlighted, setHighlighted] = useState(0);
  const [rect, setRect] = useState<{ top: number; left: number; width: number } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const { filtered, total } = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matches = needle
      ? options.filter((option) => option.toLowerCase().includes(needle))
      : options;
    return { filtered: matches.slice(0, maxResults), total: matches.length };
  }, [options, query, maxResults]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target) || popoverRef.current?.contains(target)) {
        return;
      }
      setOpen(false);
    };
    const close = () => setOpen(false);
    const onScroll = (event: Event) => {
      const target = event.target as Node | null;
      if (target && (rootRef.current?.contains(target) || popoverRef.current?.contains(target))) {
        return;
      }
      setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  useEffect(() => {
    if (open) searchRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const active = listRef.current?.querySelector('[data-active="true"]');
    active?.scrollIntoView({ block: "nearest" });
  }, [open, highlighted, filtered]);

  function openMenu() {
    if (disabled) return;
    const node = rootRef.current;
    if (node) {
      const box = node.getBoundingClientRect();
      const estimatedHeight = 330;
      const openUp =
        window.innerHeight - box.bottom < estimatedHeight && box.top > estimatedHeight;
      setRect({
        top: openUp ? box.top - estimatedHeight - 4 : box.bottom + 4,
        left: Math.max(8, Math.min(box.left, window.innerWidth - box.width - 8)),
        width: box.width,
      });
    }
    setQuery("");
    const index = options.indexOf(value);
    setHighlighted(index >= 0 ? index : 0);
    setOpen(true);
  }

  function closeMenu() {
    setOpen(false);
    setQuery("");
  }

  function selectOption(option: string) {
    onChange(option);
    closeMenu();
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlighted((current) => Math.min(current + 1, Math.max(filtered.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlighted((current) => Math.max(current - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const option = filtered[Math.min(highlighted, filtered.length - 1)];
      if (option !== undefined) selectOption(option);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeMenu();
    }
  }

  const popover =
    open && rect && !disabled && typeof document !== "undefined"
      ? createPortal(
          <div
            ref={popoverRef}
            style={{
              position: "fixed",
              top: rect.top,
              left: rect.left,
              width: rect.width,
              zIndex: 60,
            }}
            className="overflow-hidden rounded-xl border border-white/10 bg-[#151827] shadow-2xl shadow-black/50"
          >
            <div className="flex items-center gap-2 border-b border-white/8 px-3 py-2">
              <Search size={14} className="shrink-0 text-slate-500" />
              <input
                ref={searchRef}
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setHighlighted(0);
                }}
                onKeyDown={handleSearchKeyDown}
                placeholder={searchPlaceholder}
                aria-label={`Search ${ariaLabel ?? "options"}`}
                className="w-full bg-transparent text-sm text-white placeholder:text-slate-500 focus:outline-none"
              />
              {query && (
                <button
                  type="button"
                  aria-label="Clear search"
                  onClick={() => {
                    setQuery("");
                    setHighlighted(0);
                    searchRef.current?.focus();
                  }}
                  className="text-slate-500 transition hover:text-slate-300"
                >
                  <X size={14} />
                </button>
              )}
            </div>
            <div
              ref={listRef}
              role="listbox"
              aria-label={ariaLabel}
              className="max-h-64 overflow-y-auto py-1"
            >
              {filtered.length === 0 ? (
                <p className="px-3 py-3 text-sm text-slate-500">{emptyLabel}</p>
              ) : (
                filtered.map((option, index) => {
                  const selected = option === value;
                  const active = index === highlighted;
                  return (
                    <button
                      key={option}
                      type="button"
                      role="option"
                      aria-selected={selected}
                      data-active={active}
                      onMouseEnter={() => setHighlighted(index)}
                      onClick={() => selectOption(option)}
                      className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm transition ${
                        active
                          ? "bg-indigo-500/20 text-white"
                          : "text-slate-300 hover:bg-white/5"
                      }`}
                    >
                      <span className="truncate">{option}</span>
                      {selected && <Check size={14} className="shrink-0 text-indigo-300" />}
                    </button>
                  );
                })
              )}
              {total > filtered.length && (
                <p className="px-3 py-2 text-xs text-slate-500">
                  Showing first {filtered.length} of {total} — type to narrow.
                </p>
              )}
            </div>
          </div>,
          document.body
        )
      : null;

  return (
    <div ref={rootRef} className="relative w-full min-w-0">
      <button
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => (open ? closeMenu() : openMenu())}
        className="flex w-full min-w-0 items-center justify-between gap-2 rounded-lg border border-white/10 bg-[#1a1d2e] px-3 py-2 text-left text-sm transition focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <span className={`truncate ${value ? "text-white" : "text-slate-500"}`}>
          {value || placeholder}
        </span>
        <ChevronDown
          size={16}
          className={`shrink-0 text-slate-500 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {popover}
    </div>
  );
}
