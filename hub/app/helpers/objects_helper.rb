module ObjectsHelper
  LIST_FILTERS = { type: "Type", constellation: "Constellation", catalog: "Catalogue" }.freeze

  # The filters currently applied to the catalogue, each with the URL that removes it.
  def active_object_filters
    query = request.query_parameters.except("page")
    chips = []
    LIST_FILTERS.each_key do |key|
      values = list_param(key)
      values.each do |value|
        remaining = values - [ value ]
        chips << { label: value, group: LIST_FILTERS[key], url: objects_path(query.merge(key.to_s => remaining.presence).compact) }
      end
    end
    chips << { label: "Mag ≤ #{params[:mag_max]}", url: objects_path(query.except("mag_max")) } if params[:mag_max].present?
    chips << { label: "Size ≥ #{params[:size_min]}′", url: objects_path(query.except("size_min")) } if params[:size_min].present?
    chips << { label: "Well placed tonight", url: objects_path(query.except("tonight")) } if params[:tonight] == "1"
    chips << { label: "My objects", url: objects_path(query.except("mine")) } if params[:mine] == "1"
    chips
  end
end
