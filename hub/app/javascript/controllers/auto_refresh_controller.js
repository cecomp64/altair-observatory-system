import { Controller } from "@hotwired/stimulus"
import { Turbo } from "@hotwired/turbo-rails"

// Re-renders the page in place every `interval` seconds (a Turbo morph, so
// scroll position is kept), for status that changes with the clock.
export default class extends Controller {
  static values = { interval: { type: Number, default: 300 } }

  connect() {
    this.timer = setInterval(() => {
      if (document.visibilityState === "visible") Turbo.visit(window.location.href, { action: "replace" })
    }, this.intervalValue * 1000)
  }

  disconnect() {
    clearInterval(this.timer)
  }
}
