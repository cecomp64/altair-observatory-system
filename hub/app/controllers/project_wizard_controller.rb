# A guided, multi-step "new project" experience (evolved from the original
# target wizard). Each step is its own small screen:
#
#   1. objects   - catalogue search, a Telescopius lookup, or custom coordinates;
#                  several objects (or mosaic panels) become several targets
#   2. telescope - which telescope and optical train to image with
#   3. exposures - filter (from the optical train's list) x exposure x count
#   4. review    - project name and priority, then submit
#
# In-progress answers live in the session (`session[:project_wizard]`) until
# `create` persists a Project with one Target per object. Started from a
# project (`?project_id=`), the same steps add targets to that project.
class ProjectWizardController < ApplicationController
  MAX_OBJECTS = 12

  before_action :load_state
  before_action :load_existing_project

  # Step 1 ------------------------------------------------------------------

  def objects
    @query = params[:q].to_s.strip
    @results = @query.present? ? policy_scope(AstroObject).search(@query).limit(15) : []
    @objects = selected_objects
  end

  def add_object
    object = build_object_entry
    if object.nil?
      return redirect_to(new_project_path(q: params[:q]), alert: @object_error || "Couldn't add that object.")
    end

    list = (@state["objects"] ||= [])
    return redirect_to(new_project_path, alert: "A project can have at most #{MAX_OBJECTS} targets.") if list.size >= MAX_OBJECTS

    list << object
    save_state
    redirect_to new_project_path, notice: "Added #{object['name']}."
  end

  def remove_object
    (@state["objects"] ||= []).delete_at(params[:index].to_i)
    save_state
    redirect_to new_project_path
  end

  def update_objects
    return redirect_to(new_project_path, alert: "Add at least one object to image.") if selected_objects.empty?

    redirect_to project_wizard_telescope_path
  end

  # Step 2 ------------------------------------------------------------------

  def telescope
    return unless require_objects

    @telescopes = policy_scope(Telescope).active.includes(:optical_trains).order(:name)
  end

  def update_telescope
    return unless require_objects

    train = OpticalTrain.active.joins(:telescope).merge(policy_scope(Telescope).active).find_by(id: params[:optical_train_id])
    if train.nil?
      @telescopes = policy_scope(Telescope).active.includes(:optical_trains).order(:name)
      flash.now[:alert] = "Please choose a telescope to continue."
      return render :telescope, status: :unprocessable_content
    end

    @state.merge!("telescope_id" => train.telescope_id, "optical_train_id" => train.id)
    # Filters differ between trains; plans made for another train no longer apply.
    @state["exposure_plans"] = [] if @state.delete("plans_train_id").to_i != train.id
    @state["plans_train_id"] = train.id
    save_state
    redirect_to project_wizard_exposures_path
  end

  # Step 3 ------------------------------------------------------------------

  def exposures
    return unless (@optical_train = current_train_or_redirect)

    @exposure_plans = @state["exposure_plans"] || []
  end

  def add_exposure_plan
    return unless (train = current_train_or_redirect)

    filter = params[:filter].to_s.strip
    filter = train.canonical_filter(filter) if train.filter_names.any?
    seconds = params[:exposure_seconds].to_i
    count = params[:desired_count].to_i

    errors = []
    errors << "choose a filter" if filter.blank?
    errors << "enter an exposure length in seconds" unless seconds.positive?
    errors << "enter how many frames you want" unless count.positive?
    return redirect_to(project_wizard_exposures_path, alert: "Please #{errors.to_sentence}.") if errors.any?

    (@state["exposure_plans"] ||= []) << { "filter" => filter, "exposure_seconds" => seconds, "desired_count" => count }
    save_state
    redirect_to project_wizard_exposures_path
  end

  def remove_exposure_plan
    (@state["exposure_plans"] || []).delete_at(params[:index].to_i)
    save_state
    redirect_to project_wizard_exposures_path
  end

  def update_exposures
    if (@state["exposure_plans"] || []).blank?
      return redirect_to(project_wizard_exposures_path, alert: "Add at least one exposure plan before continuing.")
    end

    redirect_to project_wizard_review_path
  end

  # Step 4 ------------------------------------------------------------------

  def review
    return unless (@optical_train = current_train_or_redirect)
    return redirect_to(project_wizard_exposures_path, alert: "Add at least one exposure plan.") if (@state["exposure_plans"] || []).empty?

    @objects = selected_objects
    @project_name = @state["project_name"].presence || default_project_name
  end

  def create
    return unless (train = current_train_or_redirect)

    objects = selected_objects
    plans = @state["exposure_plans"] || []
    return redirect_to(new_project_path, alert: "Add at least one object to image.") if objects.empty?
    return redirect_to(project_wizard_exposures_path, alert: "Add at least one exposure plan.") if plans.empty?

    return add_to_existing_project(@project, train, objects, plans) if @project

    project = current_user.projects.new(
      name: params[:name].to_s.strip.presence || default_project_name,
      description: params[:description].to_s.strip.presence,
      priority: params[:priority].to_i,
      status: :active
    )
    saved = save_with_custom_objects do
      objects.each_with_index do |object, index|
        target = build_target(project, train, object, primary: index.zero?)
        plans.each { |plan| target.exposure_plans.build(plan.slice("filter", "exposure_seconds", "desired_count")) }
      end
      project.save
    end

    if saved
      clear_state
      redirect_to project, notice: "Project submitted! We'll email/Discord you as it makes progress."
    else
      errors = project.errors.full_messages + project.targets.flat_map { |t| t.errors.full_messages + t.exposure_plans.flat_map { |p| p.errors.full_messages } }
      redirect_to project_wizard_review_path, alert: errors.uniq.to_sentence
    end
  end

  private

  def build_target(project, train, object, primary:)
    project.targets.build(
      user: project.user, telescope: train.telescope, optical_train: train,
      astro_object_id: object["astro_object_id"] || custom_object_for(object, project.user).id, name: object["name"],
      ra_deg: object["ra_deg"], dec_deg: object["dec_deg"], panel: object["panel"],
      is_primary: primary, priority: project.priority, status: :submitted, submitted_at: Time.current
    )
  end

  def add_to_existing_project(project, train, objects, plans)
    targets = []
    saved = save_with_custom_objects do
      targets = objects.map do |object|
        target = build_target(project, train, object, primary: project.targets.none?(&:persisted?) && object == objects.first)
        plans.each { |plan| target.exposure_plans.build(plan.slice("filter", "exposure_seconds", "desired_count")) }
        target
      end
      targets.all?(&:save)
    end
    unless saved
      errors = targets.flat_map { |t| t.errors.full_messages + t.exposure_plans.flat_map { |p| p.errors.full_messages } }
      return redirect_to(project_wizard_review_path, alert: errors.uniq.to_sentence)
    end

    clear_state
    note = project.paused? ? " The project is paused, so they wait until you resume it." : ""
    redirect_to project, notice: "Added #{helpers.pluralize(targets.size, 'target')} to #{project.name}.#{note}"
  end

  # ?project_id= starts (or continues) adding targets to that project;
  # ?new=1 goes back to making a new project.
  def load_existing_project
    if params[:new].present?
      @state.delete("project_id")
      save_state
    elsif params[:project_id].present? && params[:project_id].to_s != @state["project_id"].to_s
      project = Project.find(params[:project_id])
      authorize project, :manage?
      @state.replace("project_id" => project.id)
      save_state
    end
    return if @state["project_id"].blank?

    @project = Project.find_by(id: @state["project_id"])
    if @project.nil? || !policy(@project).manage?
      @project = nil
      @state.delete("project_id")
      save_state
    end
  end

  # A catalogue object by id, a name to resolve (local, then Telescopius), or
  # custom coordinates.
  def build_object_entry
    if params[:astro_object_id].present?
      object = policy_scope(AstroObject).find_by(id: params[:astro_object_id])
      return object_entry(object) if object&.coordinates?

      @object_error = "That object has no coordinates."
      return nil
    end

    if params[:resolve].present?
      # Only an admin's lookup is stored in the catalogue; a member's becomes
      # their own object when the project is created.
      object, outcome = Catalogue::NameResolver.new.resolve(params[:resolve], created_by: current_user, persist: current_user.admin?)
      return object_entry(object) if object&.persisted? && object.coordinates?
      return looked_up_entry(object) if object&.coordinates?

      @object_error = {
        not_configured: "“#{params[:resolve]}” isn't in the catalogue, and online lookup isn't configured. Enter coordinates below.",
        error: "Online lookup failed. Try again, or enter coordinates below."
      }.fetch(outcome, "Couldn't find “#{params[:resolve]}”. Check the name, or enter coordinates below.")
      return nil
    end

    name = params[:name].to_s.strip
    ra = CoordinateParser.parse_ra(params[:ra])
    dec = CoordinateParser.parse_dec(params[:dec])
    errors = []
    errors << "give the target a name" if name.blank?
    errors << "enter a valid right ascension (e.g. 05:35:17 or 83.86)" if ra.nil?
    errors << "enter a valid declination (e.g. -05:23:28 or -5.39)" if dec.nil?
    if errors.any?
      @object_error = "Please #{errors.to_sentence}."
      return nil
    end

    { "name" => name, "ra_deg" => ra.round(5), "dec_deg" => dec.round(5), "panel" => params[:panel].to_s.strip.presence }
  end

  # A custom target (coordinates, or a name a member looked up) becomes the
  # owner's own private catalogue object, reused if they already have one of
  # that name within an arcminute.
  def custom_object_for(entry, owner)
    names = [ entry["name"], *entry["aliases"] ].compact.uniq
    normalized = names.filter_map { |n| Catalogue::AliasNormalizer.normalize(n) }
    existing = AstroObject.where(source: "custom", created_by: owner).joins(:aliases)
                          .where(object_aliases: { normalized_name: normalized }).distinct.find do |o|
      o.coordinates? && Astro::Coordinates.separation(entry["ra_deg"].to_f, entry["dec_deg"].to_f, o.ra_deg.to_f, o.dec_deg.to_f) <= 1.0 / 60
    end
    existing || AstroObject.create!(primary_name: entry["name"], ra_deg: entry["ra_deg"], dec_deg: entry["dec_deg"],
                                    object_type: entry["object_type"], source: "custom", source_ref: entry["looked_up"],
                                    created_by: owner).tap { |o| names.each { |n| o.add_alias(n) } }
  end

  # Custom objects are created as targets are built; roll them back with the
  # project if it doesn't save.
  def save_with_custom_objects
    saved = false
    ActiveRecord::Base.transaction do
      saved = yield
      raise ActiveRecord::Rollback unless saved
    end
    saved
  end

  def looked_up_entry(object)
    { "name" => object.primary_name, "ra_deg" => object.ra_deg.to_f, "dec_deg" => object.dec_deg.to_f,
      "object_type" => object.object_type, "aliases" => object.pending_aliases, "looked_up" => "telescopius" }
  end

  def object_entry(object)
    { "astro_object_id" => object.id, "name" => object.primary_name, "ra_deg" => object.ra_deg.to_f, "dec_deg" => object.dec_deg.to_f }
  end

  def selected_objects
    @state["objects"] || []
  end

  def default_project_name
    names = selected_objects.map { |o| o["name"] }.uniq
    names.size > 2 ? "#{names.first} + #{names.size - 1} more" : names.join(" & ")
  end

  def require_objects
    return true if selected_objects.any?

    redirect_to new_project_path, alert: "Let's start by choosing what to image."
    false
  end

  def current_train_or_redirect
    return nil unless require_objects

    train = @state["optical_train_id"] &&
      OpticalTrain.active.joins(:telescope).merge(policy_scope(Telescope).active).find_by(id: @state["optical_train_id"])
    redirect_to project_wizard_telescope_path, alert: "Pick a telescope first." if train.nil?
    train
  end

  def load_state
    @state = session[:project_wizard] ||= {}
  end

  def save_state
    session[:project_wizard] = @state
  end

  def clear_state
    session.delete(:project_wizard)
  end
end
