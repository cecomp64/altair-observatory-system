import { Controller } from "@hotwired/stimulus"

// A "select all" checkbox for a table of row checkboxes, and a count.
//   <form data-controller="bulk-select">
//     <input type="checkbox" data-action="bulk-select#toggleAll" data-bulk-select-target="all">
//     <input type="checkbox" name="frame_ids[]" data-bulk-select-target="row" data-action="bulk-select#update">
//     <span data-bulk-select-target="count"></span>
export default class extends Controller {
  static targets = ["all", "row", "count"]

  toggleAll() {
    this.rowTargets.forEach(box => { box.checked = this.allTarget.checked })
    this.update()
  }

  update() {
    const selected = this.rowTargets.filter(box => box.checked).length
    if (this.hasCountTarget) this.countTarget.textContent = selected ? `${selected} selected` : ""
  }
}
