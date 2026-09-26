module ApplicationHelper
  include Pagy::Frontend

  def sjaa_membership_url
    ENV.fetch("SJAA_MEMBERSHIP_URL", "https://www.sjaa.net/membership/")
  end

  # Signs master download links; `archive.downloadable?(product)` is false
  # when the archive reader isn't configured.
  def archive
    @archive ||= Archive::Presigner.default
  end
end
