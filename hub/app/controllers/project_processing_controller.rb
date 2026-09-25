# Processing controls on a project page: include/exclude a night, rerun,
# re-reference, multi-night mode and settings. Everything reaches Altair as
# commands (§5.4), except settings, which travel with the next config pull.
class ProjectProcessingController < ApplicationController
  SETTINGS = {
    "reference_filter" => :string, "drizzle_scale" => :integer, "keep_calibrated_frames" => :boolean,
    "pin_to_nas" => :boolean, "min_lights_per_stack" => :integer, "max_fwhm_ratio_to_project_median" => :float,
    "wbpp_profile" => :string
  }.freeze
  # A change to these needs a confirmed re-reference (§5.4).
  REREFERENCE_SETTINGS = %w[drizzle_scale reference_filter].freeze

  before_action :set_project_and_target

  def night
    kind = params[:include] == "1" ? "night_include" : "night_exclude"
    issue(kind, target_id: @target.id, night: params[:night], filter: params[:filter])
    redirect_to project_path(@project, anchor: "nights"), notice: "#{kind == 'night_include' ? 'Including' : 'Excluding'} #{params[:filter]} on #{params[:night]}."
  end

  def rerun
    issue("rerun", { target_id: @target.id, night: params[:night].presence, filter: params[:filter].presence }.compact)
    redirect_to project_path(@project, anchor: "processing"), notice: "Rerun requested."
  end

  def rereference
    return redirect_to(project_path(@project, anchor: "processing"), alert: "Tick the confirmation to re-reference.") unless params[:confirm] == "1"

    issue("rereference", { target_id: @target.id, from_night: params[:from_night].presence }.compact)
    redirect_to project_path(@project, anchor: "processing"), notice: "Re-reference requested. Every night will be re-registered."
  end

  def mode
    mode = params[:mode].presence_in(%w[master_merge frame_reintegration]) or return head(:unprocessable_content)
    @target.update!(processing_settings: @target.processing_settings.deep_merge("multi_night" => { "mode" => mode }))
    issue("set_mode", target_id: @target.id, mode: mode)
    redirect_to project_path(@project, anchor: "processing"), notice: "Multi-night mode set to #{mode.humanize.downcase}."
  end

  def settings
    changes = SETTINGS.each_with_object({}) do |(key, type), out|
      next unless params.key?(key)

      value = params[key].presence
      out[key] = value && { integer: value.to_i, float: value.to_f, boolean: value == "1", string: value }.fetch(type)
    end
    before = @target.effective_processing_settings
    @target.update!(processing_settings: @target.processing_settings.merge(changes))
    needs_rereference = REREFERENCE_SETTINGS.select { |k| before[k] != @target.effective_processing_settings[k] }
    notice = "Settings saved; Altair picks them up on its next config pull."
    notice += " #{needs_rereference.join(' and ').humanize} changed: re-reference to apply." if needs_rereference.any?
    redirect_to project_path(@project, anchor: "processing"), notice: notice
  end

  private

  def set_project_and_target
    @project = Project.find(params[:project_id])
    authorize @project, :update?
    @target = @project.targets.find(params[:target_id])
  end

  def issue(kind, payload)
    commands = Processing::CommandIssuer.issue!(kind: kind, telescope: @target.telescope, payload: payload, requested_by: current_user, target: @target)
    flash[:alert] = "No processing node serves #{@target.telescope.name} yet." if commands.empty?
  end
end
