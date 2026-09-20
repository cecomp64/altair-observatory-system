# A guided, multi-step "new target" experience. Each step is its own
# small screen with focused instructions, rather than one long form:
#
#   1. telescope  - pick which telescope to image with
#   2. details    - name + coordinates for the target
#   3. exposures  - one or more filter/duration/count exposure plans
#   4. review     - confirm everything, then submit
#
# In-progress answers live in the session (`session[:target_wizard]`)
# until the final `create` step persists them as a real Target.
class TargetWizardController < ApplicationController
  before_action :load_state

  def telescope
    @telescopes = policy_scope(Telescope)
  end

  def update_telescope
    telescope = policy_scope(Telescope).find_by(id: params[:telescope_id])

    if telescope.nil?
      @telescopes = policy_scope(Telescope)
      flash.now[:alert] = "Please choose a telescope to continue."
      return render :telescope, status: :unprocessable_content
    end

    @state["telescope_id"] = telescope.id
    save_state
    redirect_to target_wizard_details_path
  end

  def details
    @telescope = current_telescope_or_redirect
  end

  def update_details
    @telescope = current_telescope_or_redirect
    return unless @telescope

    name = params[:name].to_s.strip
    ra_deg = CoordinateParser.parse_ra(params[:ra])
    dec_deg = CoordinateParser.parse_dec(params[:dec])

    errors = []
    errors << "give the target a name" if name.blank?
    errors << "enter a valid right ascension (e.g. 05:35:17 or 83.86)" if ra_deg.nil?
    errors << "enter a valid declination (e.g. -05:23:28 or -5.39)" if dec_deg.nil?

    if errors.any?
      flash.now[:alert] = "Please #{errors.to_sentence}."
      @name, @ra, @dec, @notes = name, params[:ra], params[:dec], params[:notes]
      return render :details, status: :unprocessable_content
    end

    @state.merge!("name" => name, "ra_deg" => ra_deg, "dec_deg" => dec_deg, "notes" => params[:notes].to_s.strip)
    save_state
    redirect_to target_wizard_exposures_path
  end

  def exposures
    @telescope = current_telescope_or_redirect
    @exposure_plans = @state["exposure_plans"] || []
  end

  def add_exposure_plan
    plans = (@state["exposure_plans"] ||= [])
    plans << {
      "filter" => params[:filter].presence || "L",
      "exposure_seconds" => params[:exposure_seconds].to_i,
      "desired_count" => params[:desired_count].to_i
    }
    save_state
    redirect_to target_wizard_exposures_path
  end

  def remove_exposure_plan
    plans = @state["exposure_plans"] || []
    plans.delete_at(params[:index].to_i)
    @state["exposure_plans"] = plans
    save_state
    redirect_to target_wizard_exposures_path
  end

  def update_exposures
    if (@state["exposure_plans"] || []).blank?
      redirect_to target_wizard_exposures_path, alert: "Add at least one exposure plan before continuing."
      return
    end

    redirect_to target_wizard_review_path
  end

  def review
    @telescope = current_telescope_or_redirect
    return unless @telescope

    if @state["name"].blank?
      redirect_to target_wizard_details_path, alert: "A few details are missing — let's fill those in."
    end
  end

  def create
    telescope = current_telescope_or_redirect
    return unless telescope

    target = telescope.targets.new(
      user: current_user,
      name: @state["name"],
      ra_deg: @state["ra_deg"],
      dec_deg: @state["dec_deg"],
      notes: @state["notes"],
      status: :submitted,
      submitted_at: Time.current
    )

    (@state["exposure_plans"] || []).each do |plan|
      target.exposure_plans.build(
        filter: plan["filter"],
        exposure_seconds: plan["exposure_seconds"],
        desired_count: plan["desired_count"]
      )
    end

    if target.save
      clear_state
      redirect_to target, notice: "Target submitted! We'll email/Discord you as it makes progress."
    else
      flash[:alert] = target.errors.full_messages.to_sentence
      redirect_to target_wizard_review_path
    end
  end

  private

  def current_telescope_or_redirect
    telescope = @state["telescope_id"] && policy_scope(Telescope).find_by(id: @state["telescope_id"])
    redirect_to new_target_path, alert: "Let's start by picking a telescope." if telescope.nil?
    telescope
  end

  def load_state
    @state = session[:target_wizard] ||= {}
  end

  def save_state
    session[:target_wizard] = @state
  end

  def clear_state
    session.delete(:target_wizard)
  end
end
