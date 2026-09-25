# Fetches a survey cut-out for an object from NASA SkyView (port of
# astrophotography-database showcase_service.py; asks SkyView for a JPEG
# directly instead of stretching a FITS).
class ShowcaseSurveyFetchJob < ApplicationJob
  SKYVIEW_URL = "https://skyview.gsfc.nasa.gov/current/cgi/runquery.pl".freeze

  queue_as :default
  retry_on Faraday::Error, wait: :polynomially_longer, attempts: 3

  def perform(astro_object_id, survey = "DSS2 Red", connection: nil)
    object = AstroObject.find(astro_object_id)
    return unless object.coordinates?

    size = object.size_major_arcmin ? [ 0.25, object.size_major_arcmin.to_f / 60 * 2 ].max : 0.5
    connection ||= Faraday.new(request: { timeout: 90 })
    response = connection.get(SKYVIEW_URL, {
      Position: "#{object.ra_deg.to_f},#{object.dec_deg.to_f}", Survey: survey, Pixels: 800,
      Size: size.round(3), Return: "JPEG", Scaling: "Log"
    })
    raise "SkyView returned HTTP #{response.status}" unless response.success?
    raise "SkyView didn't return an image" unless response.body.to_s.b.start_with?("\xFF\xD8".b)

    showcase = object.showcase || object.build_showcase
    showcase.update!(source_type: "survey", survey_name: survey, data_product: nil)
    showcase.image.attach(io: StringIO.new(response.body), filename: "#{object.id}_#{survey.parameterize}.jpg", content_type: "image/jpeg")
  end
end
