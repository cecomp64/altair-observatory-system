FactoryBot.define do
  factory :telescope do
    sequence(:name) { |n| "Test Telescope #{n}" }
    latitude { 37.34 }
    longitude { -121.89 }
    elevation_m { 100 }
    active { true }
    self_serve_submit { true }
    description { "A telescope used in tests." }
  end
end
