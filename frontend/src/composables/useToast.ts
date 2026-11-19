import { ref } from "vue";

export function useToast() {
  const toast = ref("");
  let timer: number | undefined;

  function notify(message: string) {
    toast.value = message;
    window.clearTimeout(timer);
    timer = window.setTimeout(() => { toast.value = ""; }, 3600);
  }

  return { toast, notify };
}

