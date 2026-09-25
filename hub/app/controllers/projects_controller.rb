class ProjectsController < ApplicationController
  before_action :set_project, only: [ :show, :edit, :update ]

  def index
    @scope = params[:scope] == "club" ? "club" : "mine"
    projects = policy_scope(Project).includes(targets: [ :exposure_plans, :telescope ])
    projects = @scope == "club" ? projects.visibility_club.where.not(user: current_user) : projects.where(user: current_user)
    @projects = projects.recent_first
  end

  def show
    authorize @project
    @targets = @project.targets.includes(:exposure_plans, :telescope, :optical_train, :astro_object).order(:id)
    @progress = Progress::Calculator.new(@project)
    load_processing
    @visibility = @targets.group_by(&:telescope).map do |telescope, targets|
      site = Astro::Site.for(telescope)
      visibility = Astro::Visibility.new(site)
      night = visibility.night(telescope.night_for(Time.current))
      results = targets.map { |t| visibility.for_night(t.ra_deg, t.dec_deg, night.date, min_altitude: t.effective_min_altitude_deg) }
      primary = targets.find(&:is_primary?) || targets.first
      best = visibility.best_viewing(primary.ra_deg, primary.dec_deg, min_altitude: primary.effective_min_altitude_deg)
      { telescope: telescope, night: night, targets: targets, results: results, best: best, primary: primary }
    end
  end

  def edit
    authorize @project
  end

  def update
    authorize @project
    if @project.update(project_params)
      redirect_to @project, notice: "Project updated."
    else
      render :edit, status: :unprocessable_content
    end
  end

  private

  # Nights, masters, quality, issues and commands for the project page.
  def load_processing
    lights = Frame.lights.counted.where(project: @project)
    rows = lights.group(:target_id, :night, :filter)
                 .pluck(:target_id, :night, :filter, Arel.sql("count(*)"), Arel.sql("coalesce(sum(exposure_s), 0)"),
                        Arel.sql("avg((quality->>'fwhm')::float)"), Arel.sql("avg((quality->>'eccentricity')::float)"))
    products = @project.data_products.masters.current.includes(preview_attachment: :blob, thumbnail_attachment: :blob)
    @multi_masters = products.select(&:multi_night_master?).group_by { |p| [ p.target_id, p.filter ] }
                             .transform_values { |list| list.max_by { |p| [ p.version.to_i, p.id ] } }
    night_masters = products.select { |p| p.night_master? || p.provisional_noflat? }.index_by { |p| [ p.target_id, p.night, p.filter ] }
    @nights = rows.map do |target_id, night, filter, count, seconds, fwhm, ecc|
      weights = @multi_masters[[ target_id, filter ]]&.metrics&.dig("night_weights") || {}
      { target_id: target_id, night: night, filter: filter, count: count, hours: (seconds.to_f / 3600).round(2),
        fwhm: fwhm&.round(2), eccentricity: ecc&.round(2), master: night_masters[[ target_id, night, filter ]],
        weight: weights[night.iso8601], in_merge: weights.key?(night.iso8601) }
    end.sort_by { |n| [ -n[:night].jd, n[:filter].to_s ] }
    @cumulative = @nights.group_by { |n| n[:filter] }.transform_values do |list|
      total = 0.0
      list.sort_by { |n| n[:night] }.group_by { |n| n[:night] }.map { |night, ns| [ night.iso8601, (total += ns.sum { |n| n[:hours] }).round(2) ] }
    end
    @issues = @project.processing_issues.open.recent_first.includes(:target)
    @commands = ProcessingCommand.where(target_id: @targets.map(&:id)).recent_first.limit(15).includes(:requested_by)
    @served = @targets.index_with { |t| t.telescope.processing_nodes.active.any? }
  end

  def set_project
    @project = Project.find(params[:id])
  end

  def project_params
    params.require(:project).permit(:name, :description, :status, :priority, :visibility, :completion_basis)
  end
end
