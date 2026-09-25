class FramesController < ApplicationController
  PER_PAGE = 50

  def index
    authorize Frame
    @search = Frames::Search.new(policy_scope(Frame), search_params)
    respond_to do |format|
      format.html do
        @stats = @search.stats
        @groups = @search.groups
        @pagy, @frames = pagy(@search.relation.order(date_obs: :desc, id: :desc).includes(:target, :telescope, :optical_train), limit: PER_PAGE)
        @telescopes = policy_scope(Telescope).order(:name)
        @filters = policy_scope(Frame).distinct.order(:filter).pluck(:filter).compact
        @assignable_targets = assignable_targets
      end
      format.csv do
        send_data @search.to_csv(@search.relation), filename: "frames-#{Date.current}.csv", type: "text/csv"
      end
    end
  end

  def show
    @frame = Frame.includes(frame_objects: :astro_object).find(params[:id])
    authorize @frame
    @products = @frame.target ? @frame.target.data_products.masters.where(filter: @frame.filter).order(created_at: :desc).limit(10) : []
    @used_in = @products.select { |p| Array(p.metrics["frame_sha256s"]).include?(@frame.sha256) }
  end

  # Lights no rule could link to a target, grouped by night + OBJECT, each
  # group with the nearest target on that telescope as a suggestion.
  def unassigned
    authorize Frame
    rows = Frame.unassigned.group(:telescope_id, :night, :object_header)
                .pluck(:telescope_id, :night, :object_header, Arel.sql("count(*)"), Arel.sql("avg(ra_deg)"), Arel.sql("avg(dec_deg)"))
    telescopes = Telescope.where(id: rows.map(&:first)).index_by(&:id)
    candidates = Target.not_draft.where(telescope_id: telescopes.keys).to_a.group_by(&:telescope_id)
    @groups = rows.sort_by { |r| [ -r[1].jd, r[2].to_s ] }.map do |telescope_id, night, object, count, ra, dec|
      suggestion = ra && dec && candidates.fetch(telescope_id, []).min_by { |t| Astro::Coordinates.separation(ra.to_f, dec.to_f, t.ra_deg.to_f, t.dec_deg.to_f) }
      distance = suggestion && Astro::Coordinates.separation(ra.to_f, dec.to_f, suggestion.ra_deg.to_f, suggestion.dec_deg.to_f)
      { telescope: telescopes[telescope_id], night: night, object: object, count: count, suggestion: suggestion,
        distance: distance, targets: candidates.fetch(telescope_id, []) }
    end
  end

  # Bulk assign: frame ids, or a (telescope, night, OBJECT) group.
  def assign
    authorize Frame
    target = Target.find(params[:target_id])
    frames = if params[:frame_ids].present?
      policy_scope(Frame).where(id: params[:frame_ids])
    else
      Frame.unassigned.where(telescope_id: params[:telescope_id], night: params[:night], object_header: params[:object_header].presence)
    end
    count = Frames::Assigner.assign!(frames, target: target, user: current_user)
    redirect_back fallback_location: frames_path, notice: "Assigned #{count} frame#{'s' unless count == 1} to #{target.name}; Altair will re-plan."
  rescue ArgumentError, ActiveRecord::RecordNotFound => e
    redirect_back fallback_location: frames_path, alert: e.message
  end

  private

  def search_params
    params.permit(*Frames::Search::FILTERS, :group).to_h
  end

  def assignable_targets
    return [] unless current_user.admin?

    Target.not_draft.includes(:telescope).order(:name).limit(500)
  end
end
