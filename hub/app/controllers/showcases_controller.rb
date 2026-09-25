# The picture on an object page: an upload (JPEG/PNG, <= 10 MB) or a survey
# cut-out fetched in the background from NASA SkyView.
class ShowcasesController < ApplicationController
  MAX_UPLOAD = 10.megabytes
  SURVEYS = [ "DSS2 Red", "DSS2 Blue", "DSS", "SDSSr" ].freeze

  before_action :set_object

  def create
    authorize @object, :manage_showcase?
    if params[:survey].present?
      survey = SURVEYS.include?(params[:survey]) ? params[:survey] : SURVEYS.first
      ShowcaseSurveyFetchJob.perform_later(@object.id, survey)
      return redirect_to object_path(@object), notice: "Fetching the #{survey} image — refresh in a minute."
    end

    upload = params[:image]
    unless upload.respond_to?(:content_type) && %w[image/jpeg image/png].include?(upload.content_type) && upload.size <= MAX_UPLOAD
      return redirect_to(object_path(@object), alert: "Upload a JPEG or PNG up to 10 MB.")
    end

    showcase = @object.showcase || @object.build_showcase
    showcase.update!(source_type: "upload", survey_name: nil, data_product: nil)
    showcase.image.attach(upload)
    redirect_to object_path(@object), notice: "Showcase updated."
  end

  def destroy
    authorize @object, :manage_showcase?
    @object.showcase&.destroy
    redirect_to object_path(@object), notice: "Showcase removed."
  end

  private

  def set_object
    @object = AstroObject.find(params[:object_id])
  end
end
