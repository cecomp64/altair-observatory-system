module ChartsHelper
  # Renders a <canvas> wired up to the Stimulus `chart` controller.
  def chart_tag(type:, data:, options: {}, html: {})
    tag.canvas(
      "",
      **html.merge(
        data: {
          controller: "chart",
          chart_type_value: type,
          chart_data_value: data.to_json,
          chart_options_value: options.to_json
        }
      )
    )
  end
end
