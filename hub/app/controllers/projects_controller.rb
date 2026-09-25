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

  def set_project
    @project = Project.find(params[:id])
  end

  def project_params
    params.require(:project).permit(:name, :description, :status, :priority, :visibility, :completion_basis)
  end
end
