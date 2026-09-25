FactoryBot.define do
  factory :astro_object do
    sequence(:primary_name) { |n| "Object #{n}" }
    ra_deg { 10.68471 }
    dec_deg { 41.26875 }
    object_type { "Galaxy" }
    source { "custom" }
  end
end
