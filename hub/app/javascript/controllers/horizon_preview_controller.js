import { Controller } from "@hotwired/stimulus"

// Shows an object's altitude and its horizon limit on a chart while its row
// is hovered or focused. The chart's preview datasets
// (AstroChartsHelper#altitude_chart) are hidden until then and matched by
// `previewKey`.
//
//   <div data-controller="horizon-preview">
//     <canvas data-horizon-preview-target="chart" data-controller="chart" …>
//     <li data-action="mouseenter->horizon-preview#show mouseleave->horizon-preview#hide"
//         data-horizon-preview-key-param="object-42">
export default class extends Controller {
  static targets = ["chart"]

  show({ params: { key } }) {
    this.toggle(key, true)
  }

  hide({ params: { key } }) {
    this.toggle(key, false)
  }

  toggle(key, visible) {
    const chart = this.hasChartTarget && this.application.getControllerForElementAndIdentifier(this.chartTarget, "chart")?.chart
    if (!chart) return

    let changed = false
    chart.data.datasets.forEach((dataset, index) => {
      if (dataset.previewKey !== key || chart.isDatasetVisible(index) === visible) return

      chart.setDatasetVisibility(index, visible)
      changed = true
    })
    if (changed) chart.update("none")
  }
}
