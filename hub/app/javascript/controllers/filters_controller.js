import { Controller } from "@hotwired/stimulus"

// Auto-applies a filter form as it changes (debounced, so ticking several
// boxes costs one request) and narrows long checkbox lists in place.
export default class extends Controller {
  static values = { delay: { type: Number, default: 600 } }

  disconnect() { clearTimeout(this.timer) }

  submit() {
    clearTimeout(this.timer)
    this.timer = setTimeout(() => this.element.requestSubmit(), this.delayValue)
  }

  // Keep an unnamed helper input from submitting the form.
  stop(event) { event.stopPropagation() }

  // Hide the options of one list that don't contain what was typed.
  narrow(event) {
    const needle = event.target.value.trim().toLowerCase()
    event.target.closest("[data-filter-list]").querySelectorAll("[data-option]").forEach((option) => {
      option.hidden = needle !== "" && !option.dataset.option.toLowerCase().includes(needle)
    })
  }
}
