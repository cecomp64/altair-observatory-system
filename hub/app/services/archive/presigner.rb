module Archive
  # Short-lived download links for masters in Altair's S3 archive (§12). The
  # Hub holds only a read-only "archive reader" key, and signs only keys under
  # the archive's projects/ and calibration/masters/ trees: raw frames are
  # never downloadable from the Hub. Signing is local (no request to AWS).
  #
  # Configured from ARCHIVE_READER_* environment variables, falling back to
  # Rails credentials `archive_reader: { access_key_id, secret_access_key,
  # region, bucket, prefix }`. Unconfigured, downloads are simply not offered.
  class Presigner
    EXPIRES_IN = 10.minutes
    DOWNLOADABLE_TREES = %w[projects/ calibration/masters/].freeze

    class NotDownloadable < StandardError; end

    def self.default
      new(**config)
    end

    def self.config
      creds = Rails.application.credentials.archive_reader || {}
      {
        access_key_id: ENV["ARCHIVE_READER_ACCESS_KEY_ID"] || creds[:access_key_id],
        secret_access_key: ENV["ARCHIVE_READER_SECRET_ACCESS_KEY"] || creds[:secret_access_key],
        region: ENV["ARCHIVE_READER_REGION"] || creds[:region],
        bucket: ENV["ARCHIVE_BUCKET"] || creds[:bucket],
        prefix: ENV["ARCHIVE_PREFIX"] || creds[:prefix] || "altair/"
      }
    end

    attr_reader :bucket, :prefix

    def initialize(access_key_id:, secret_access_key:, region:, bucket:, prefix: "altair/")
      @access_key_id = access_key_id
      @secret_access_key = secret_access_key
      @region = region
      @bucket = bucket
      @prefix = prefix.to_s.sub(%r{\A/}, "").then { |p| p.empty? || p.end_with?("/") ? p : "#{p}/" }
    end

    def enabled?
      [ @access_key_id, @secret_access_key, @region, @bucket ].all?(&:present?)
    end

    def downloadable?(product)
      enabled? && product.archive_uri.present? && key_for(product.archive_uri).present?
    end

    # A presigned GET for the product's archive object, served as an attachment.
    def url_for(product)
      raise NotDownloadable, "archive downloads are not configured" unless enabled?

      key = key_for(product.archive_uri.to_s) or raise NotDownloadable, "#{product.archive_uri} is not a downloadable master"
      signer.presigned_url(:get_object, bucket: bucket, key: key, expires_in: EXPIRES_IN.to_i,
                                        response_content_disposition: %(attachment; filename="#{File.basename(key)}"))
    end

    # The object key when the URI names this bucket and a downloadable tree.
    def key_for(uri)
      match = uri.to_s.match(%r{\As3://([^/]+)/(.+)\z})
      return nil unless match && match[1] == bucket

      key = match[2]
      return nil if key.include?("..")

      DOWNLOADABLE_TREES.any? { |tree| key.start_with?("#{prefix}#{tree}") } ? key : nil
    end

    private

    def signer
      require "aws-sdk-s3"
      @signer ||= Aws::S3::Presigner.new(client: Aws::S3::Client.new(
        region: @region, credentials: Aws::Credentials.new(@access_key_id, @secret_access_key)
      ))
    end
  end
end
