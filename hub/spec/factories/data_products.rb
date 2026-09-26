FactoryBot.define do
  factory :data_product do
    target
    kind { :night_master }
    sequence(:altair_id) { |n| n }
    filter { "Luminance" }
    night { Date.current }
    captured_at { Time.current }
  end
end
