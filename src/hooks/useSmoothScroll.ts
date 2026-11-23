import { useEffect, useRef } from "react";
import Lenis from "lenis";

let activeLenis: Lenis | null = null;

/**
 * Smoothly scrolls to an element, CSS selector, or vertical pixel offset
 * using the active Lenis instance on desktop or native scroll on touch/reduced-motion.
 */
export function scrollToSmooth(target: string | number | HTMLElement, offset = -76) {
  if (typeof window === "undefined") return;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (activeLenis && !reducedMotion) {
    activeLenis.scrollTo(target, {
      offset,
      duration: 1.05,
    });
    return;
  }

  if (typeof target === "number") {
    window.scrollTo({ top: target, behavior: reducedMotion ? "instant" : "smooth" });
    return;
  }

  const el = typeof target === "string" ? document.querySelector<HTMLElement>(target) : target;
  if (el) {
    el.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
  }
}

export function resetScrollTopInstant() {
  if (typeof window === "undefined") return;
  if (activeLenis) {
    activeLenis.scrollTo(0, { immediate: true });
  } else {
    window.scrollTo({ top: 0, behavior: "instant" });
  }
}

/**
 * Enables butter-smooth inertial wheel scrolling via Lenis on pointer/desktop devices
 * while NEVER intercepting trackpad/mouse-wheel scrolling inside modals, dialogs, or drawers.
 */
export function useSmoothScroll(paused: boolean) {
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  useEffect(() => {
    if (typeof window === "undefined") return;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const coarsePointer = window.matchMedia("(pointer: coarse)").matches;

    if (reducedMotion || coarsePointer) {
      return;
    }

    const lenis = new Lenis({
      duration: 1.05,
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      smoothWheel: true,
      syncTouch: false,
      wheelMultiplier: 0.95,
      // CRITICAL: Return true whenever paused OR when event originates inside any modal/dialog/data-lenis-prevent
      // so native trackpad & mouse-wheel scrolling works smoothly inside forms & 360° plot inspector cards.
      prevent: (node) => {
        if (pausedRef.current) return true;
        if (node instanceof Element) {
          return Boolean(
            node.closest(
              '[role="dialog"], [aria-modal="true"], [data-lenis-prevent], [data-lenis-prevent-wheel]',
            ),
          );
        }
        return false;
      },
    });

    activeLenis = lenis;
    let rafId = 0;

    const raf = (time: number) => {
      lenis.raf(time);
      rafId = window.requestAnimationFrame(raf);
    };
    rafId = window.requestAnimationFrame(raf);

    return () => {
      window.cancelAnimationFrame(rafId);
      lenis.destroy();
      if (activeLenis === lenis) {
        activeLenis = null;
      }
    };
  }, []);

  // Lock background page scroll cleanly while a modal is open WITHOUT calling lenis.stop()
  // (lenis.stop() globally calls preventDefault on wheel events and breaks trackpad scrolling inside modals)
  useEffect(() => {
    if (typeof document === "undefined") return;
    if (paused) {
      const prevOverflow = document.documentElement.style.overflow;
      document.documentElement.style.overflow = "hidden";
      return () => {
        document.documentElement.style.overflow = prevOverflow;
      };
    }
  }, [paused]);
}
