import { Controller } from "@hotwired/stimulus"

// A <details> menu that closes on an outside click, on Escape, and when
// another menu opens.
export default class extends Controller {
  connect() {
    this.outside = (event) => { if (!this.element.contains(event.target)) this.element.open = false }
    this.escape = (event) => { if (event.key === "Escape") this.element.open = false }
    document.addEventListener("click", this.outside)
    document.addEventListener("keydown", this.escape)
  }

  disconnect() {
    document.removeEventListener("click", this.outside)
    document.removeEventListener("keydown", this.escape)
  }
}
