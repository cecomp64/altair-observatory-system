FactoryBot.define do
  factory :data_product do
    target
    url { "https://example-bucket.s3.amazonaws.com/sub_0001.fits" }
    kind { :sub }
    filter { "Luminance" }
    captured_at { Time.current }
  end
end
